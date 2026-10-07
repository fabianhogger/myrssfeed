"""Command-line interface.

Configuration is layered, lowest precedence first: a config file, then
environment variables, then the flags given on the command line.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO

from . import __version__
from .config import (
    EFFORT_LEVELS,
    OUTPUT_FORMATS,
    Config,
    parse_interval,
    read_config_file,
)
from .errors import ConfigError, MyFeedError
from .feeds import FeedReader
from .models import Match, Verdict
from .output import render
from .runner import Runner
from .state import State

__all__ = ["build_parser", "main"]

_LOG = logging.getLogger("myfeed")

#: Exit statuses, so callers (and cron) can tell the cases apart.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3

_EPILOG = """\
examples:
  # one-off run
  myfeed --prompt "AI policy and chip export controls" \\
         --feed https://example.com/feed.xml

  # read everything from a config file and run every 30 minutes
  myfeed --config myfeed.toml --interval 30m

  # see what would be judged, without calling the API
  myfeed --config myfeed.toml --dry-run

exit status:
  0  ran successfully
  1  the run failed
  2  bad arguments or configuration
  3  ran, but some feeds or batches failed

The API key is read from ANTHROPIC_API_KEY unless --api-key is given.
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="myfeed",
        description=(
            "Read RSS feeds and print only the items that match your criteria, "
            "as judged by Claude."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"myfeed {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        type=Path,
        help="TOML or JSON config file; other options override its values",
    )

    criteria = parser.add_argument_group("criteria")
    criteria.add_argument(
        "-p",
        "--prompt",
        metavar="TEXT",
        help="system prompt describing which news you want to see",
    )
    criteria.add_argument(
        "--prompt-file",
        metavar="PATH",
        help="read the system prompt from a file ('-' for stdin)",
    )

    feeds = parser.add_argument_group("feeds")
    feeds.add_argument(
        "-f",
        "--feed",
        metavar="URL",
        action="append",
        default=[],
        dest="feed",
        help="feed URL; repeat for several feeds",
    )
    feeds.add_argument(
        "--feeds-file",
        metavar="PATH",
        type=Path,
        help="file with one feed URL per line ('#' starts a comment)",
    )
    feeds.add_argument(
        "--max-items",
        metavar="N",
        type=int,
        help="consider at most N newest entries per feed (0 for no limit)",
    )

    model = parser.add_argument_group("model")
    model.add_argument("--api-key", metavar="KEY", help="Anthropic API key")
    model.add_argument("--model", metavar="ID", help="Claude model id")
    model.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        help="how hard the model should think about each item",
    )
    model.add_argument(
        "--batch-size", metavar="N", type=int, help="items judged per API request"
    )
    model.add_argument(
        "--max-tokens", metavar="N", type=int, help="output token ceiling per request"
    )
    model.add_argument(
        "--no-refusal-fallback",
        action="store_true",
        help="fail a declined request instead of routing it to a fallback model",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "-o", "--format", choices=OUTPUT_FORMATS, dest="format", help="output format"
    )
    output.add_argument(
        "--no-reasons",
        action="store_true",
        help="do not ask for or print a reason per item",
    )

    scheduling = parser.add_argument_group("scheduling")
    scheduling.add_argument(
        "-i",
        "--interval",
        metavar="DURATION",
        help="keep running, waiting this long between runs (e.g. 45m, 2h)",
    )
    scheduling.add_argument(
        "--once",
        action="store_true",
        help="run a single pass even if an interval is configured",
    )

    state = parser.add_argument_group("state")
    state.add_argument(
        "--state",
        metavar="PATH",
        type=Path,
        help="path to the file recording which items were already handled",
    )
    state.add_argument(
        "--no-state",
        action="store_true",
        help="do not read or write state; reconsider every item",
    )
    state.add_argument(
        "--state-ttl-days",
        metavar="N",
        type=int,
        help="how long handled items are remembered",
    )

    debug = parser.add_argument_group("diagnostics")
    debug.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="list the items that would be judged, without calling the API",
    )
    debug.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="more logging; repeat for debug level",
    )
    debug.add_argument("-q", "--quiet", action="store_true", help="only log errors")
    return parser


def main(argv: Optional[Sequence[str]] = None, stream: Optional[TextIO] = None) -> int:
    """Entry point. Returns the process exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stream if stream is not None else sys.stdout
    _configure_logging(args.verbose, args.quiet)

    try:
        config = _build_config(args)
    except ConfigError as exc:
        # Returned rather than raised, so every outcome of main() is an exit
        # code. Argparse still raises SystemExit for malformed flags.
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        if args.dry_run:
            return _dry_run(config, out)
        runner = Runner(config)
        if config.interval and not args.once:
            _LOG.info("running every %ds; press Ctrl-C to stop", config.interval)
            runner.run_forever(out)
            return EXIT_OK
        result = runner.run_once()
        runner.report(result, out)
        for error in result.feed_errors:
            _LOG.error("feed failed: %s", error)
        return EXIT_OK if result.ok else EXIT_PARTIAL
    except KeyboardInterrupt:
        _LOG.info("interrupted")
        return EXIT_OK
    except MyFeedError as exc:
        _LOG.error("%s", exc)
        return EXIT_ERROR


# -- configuration assembly ----------------------------------------------


def _build_config(args: argparse.Namespace) -> Config:
    """Merge config file, environment and flags into a validated Config."""
    data: Dict[str, Any] = {}
    if args.config is not None:
        data.update(read_config_file(args.config))
    data.update(Config.env_overrides())
    data.update(_cli_overrides(args))

    if "system_prompt" not in data or not str(data.get("system_prompt", "")).strip():
        raise ConfigError(
            "no system prompt: pass --prompt/--prompt-file, set MYFEED_SYSTEM_PROMPT, "
            "or put 'system_prompt' in a config file"
        )
    if not data.get("feeds"):
        raise ConfigError(
            "no feeds: pass --feed/--feeds-file, set MYFEED_FEEDS, or put 'feeds' "
            "in a config file"
        )
    return Config.from_mapping(data)


def _read_bytes(path: Path) -> bytes:
    try:
        return Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def _cli_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    """Collect the options actually given on the command line."""
    overrides: Dict[str, Any] = {}
    prompt = _resolve_prompt(args)
    if prompt is not None:
        overrides["system_prompt"] = prompt

    feeds = list(args.feed)
    if args.feeds_file is not None:
        feeds.extend(_read_feed_list(args.feeds_file))
    if feeds:
        overrides["feeds"] = feeds

    simple = {
        "api_key": args.api_key,
        "model": args.model,
        "effort": args.effort,
        "batch_size": args.batch_size,
        "max_tokens": args.max_tokens,
        "max_items_per_feed": args.max_items,
        "output_format": args.format,
        "state_ttl_days": args.state_ttl_days,
    }
    overrides.update({key: value for key, value in simple.items() if value is not None})

    if args.interval is not None:
        overrides["interval"] = parse_interval(args.interval)
    if args.once:
        overrides["interval"] = None
    if args.no_reasons:
        overrides["include_reasons"] = False
    if args.no_refusal_fallback:
        overrides["refusal_fallback"] = False
    if args.no_state:
        overrides["state_path"] = None
    elif args.state is not None:
        overrides["state_path"] = args.state
    return overrides


def _resolve_prompt(args: argparse.Namespace) -> Optional[str]:
    """Read the prompt from ``--prompt`` or ``--prompt-file``."""
    if args.prompt and args.prompt_file:
        raise ConfigError("use either --prompt or --prompt-file, not both")
    if args.prompt:
        return args.prompt
    if args.prompt_file:
        if args.prompt_file == "-":
            return sys.stdin.read()
        return _read_bytes(Path(args.prompt_file)).decode("utf-8")
    return None


def _read_feed_list(path: Path) -> List[str]:
    """Read feed URLs from a file, one per line, ignoring blanks and comments."""
    text = _read_bytes(path).decode("utf-8")
    urls = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            urls.append(stripped)
    if not urls:
        raise ConfigError(f"{path} contains no feed URLs")
    return urls


# -- dry run --------------------------------------------------------------


def _dry_run(config: Config, out: TextIO) -> int:
    """Fetch feeds and show what would be sent to the model."""
    state = State.load(config.state_path, ttl_days=config.state_ttl_days)
    reader = FeedReader(
        state,
        max_items_per_feed=config.max_items_per_feed,
        user_agent=config.user_agent,
    )
    items, errors = reader.read(config.feeds)
    # Deliberately not saved: a dry run must not make items look handled.
    placeholder = Verdict(relevant=True, reason="not judged (dry run)")
    matches = [Match(item=item, verdict=placeholder) for item in items]
    out.write(render(matches, config.output_format, show_reasons=False))
    out.flush()
    batches = -(-len(items) // config.batch_size) if items else 0
    _LOG.info(
        "dry run: %d new item(s) would be judged in %d request(s) to %s",
        len(items),
        batches,
        config.model,
    )
    for error in errors:
        _LOG.error("feed failed: %s", error)
    return EXIT_OK if not errors else EXIT_PARTIAL


def _configure_logging(verbosity: int, quiet: bool) -> None:
    """Send logs to stderr so stdout stays machine-readable."""
    if quiet:
        level = logging.ERROR
    elif verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
