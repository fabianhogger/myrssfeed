# myrssfeed

Read RSS/Atom feeds, and print only the items that match criteria you describe
in plain English. Claude makes the call on each item; you get the headline, the
link, and a one-line reason.

```console
$ myrssfeed --prompt "Export controls on AI chips, and EU tech regulation" \
         --feed https://feeds.arstechnica.com/arstechnica/technology-lab \
         --feed https://www.theregister.com/headlines.atom
US tightens accelerator export rules for third-country resellers
  https://www.theregister.com/2026/10/06/export_rules/
  The Register · 2026-10-06T09:12:00+00:00
  why: Directly about chip export controls.

EU Commission opens consultation on cloud switching rules
  https://arstechnica.com/2026/10/06/eu-cloud/
  Ars Technica · 2026-10-06T07:40:00+00:00
  why: EU tech regulation affecting cloud providers.
```

Everything else in those feeds is dropped silently.

## Why

Feed readers filter on keywords, which is both too narrow ("accelerator" misses
"GPU") and too broad (every mention of "chips" matches). A prompt describes what
you actually want: *"new CVEs in Python packages I'd have to patch, not general
security commentary."*

## Install

```bash
pip install myrssfeed
```

From a checkout, for development:

```bash
git clone https://github.com/fabianhogger/myrssfeed
cd myrssfeed
pip install -e ".[dev]"
```

Requires Python 3.9+ and an Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Check that it landed:

```bash
myrssfeed --version
```

If the `myrssfeed` command is not found, your Python scripts directory is not on
`PATH`; `python -m myrssfeed` works identically.

## Usage

### One-off run

```bash
myrssfeed --prompt "Rust async ecosystem news" --feed https://blog.rust-lang.org/feed.xml
```

### With a config file

`myrssfeed.toml`:

```toml
system_prompt = """
Keep items about:
  - security advisories affecting Python or Node packages
  - outages at major cloud providers

Skip funding rounds, opinion pieces, and conference announcements.
"""

feeds = [
  "https://feeds.feedburner.com/TheHackersNews",
  "https://status.cloud.google.com/en/feed.atom",
]

interval = "30m"
state_path = "~/.local/state/myrssfeed/state.json"
output_format = "markdown"
```

```bash
myrssfeed --config myrssfeed.toml
```

JSON config files work the same way; the format is chosen by file extension.

### Scheduling

Two options, depending on whether you want a resident process.

**Built-in loop** — runs until you stop it, and shuts down cleanly on
`Ctrl-C`/`SIGTERM` so the state file is never left half-written:

```bash
myrssfeed --config myrssfeed.toml --interval 45m
```

**cron or systemd** — have myrssfeed run one pass and exit. The state file means
each run only reports items it has not reported before:

```cron
*/30 * * * * ANTHROPIC_API_KEY=sk-ant-... /usr/local/bin/myrssfeed --config ~/myrssfeed.toml --once >> ~/myrssfeed.log 2>&1
```

A systemd timer unit is in [`examples/`](examples/).

### Checking a setup before spending anything

`--dry-run` fetches the feeds and lists what *would* be judged, without calling
the API, and without marking anything as seen:

```bash
myrssfeed --config myrssfeed.toml --dry-run -v
```

### Output formats

| `--format`  | For                                               |
| ----------- | ------------------------------------------------- |
| `text`      | Reading in a terminal (default)                   |
| `markdown`  | Pasting into notes, or posting to chat            |
| `json`      | Piping into `jq` or another program               |

Logs go to stderr and results to stdout, so `myrssfeed ... | jq` works as expected.

## How it works

1. Each feed is fetched with `If-None-Match`/`If-Modified-Since`, so an unchanged
   feed costs one small request and no tokens.
2. Entries already handled on an earlier run are dropped using the state file.
3. The rest are sent to Claude in batches, with your prompt as the system prompt
   and a JSON schema constraining the reply to one verdict per item.
4. Matching items are printed; every item that got a verdict is recorded as seen.

Items in a batch that fails (a transport error, a declined request) are *not*
recorded, so the next run retries them rather than dropping them silently.

## Cost

Only titles, summaries and dates are sent — never full articles — and summaries
are truncated. The system prompt is cached across the batches of a run.

The knobs that matter:

- `--batch-size` (default 10): larger batches spread the prompt over more items.
- `--effort` (default `low`): relevance is a classification task and rarely
  needs more. Raise it if the criteria are subtle.
- `--no-reasons`: drops the per-item justification, and the output tokens with it.
- `--max-items N`: caps how many entries per feed are considered at all.

## Configuration reference

Every key below can be set in a config file, as an environment variable
`MYRSSFEED_<KEY>` (uppercase), or as a command-line flag. Precedence is
file < environment < flags.

| Key                  | Default            | Meaning                                                |
| -------------------- | ------------------ | ------------------------------------------------------ |
| `system_prompt`      | *required*         | Your criteria                                          |
| `feeds`              | *required*         | Feed URLs (comma-separated in the environment)          |
| `api_key`            | `ANTHROPIC_API_KEY`| Anthropic API key                                      |
| `model`              | `claude-opus-5-5`  | Claude model id                                        |
| `effort`             | `low`              | `low`, `medium`, `high`, `xhigh`, `max`                |
| `batch_size`         | `10`               | Items per API request                                  |
| `max_items_per_feed` | `50`               | Newest entries considered per feed; `0` for no limit    |
| `state_path`         | *none*             | Where handled items are recorded                       |
| `state_ttl_days`     | `30`               | How long they are remembered                           |
| `output_format`      | `text`             | `text`, `markdown`, `json`                             |
| `interval`           | *none*             | Run repeatedly, e.g. `30m`, `2h`, `1h30m`              |
| `timeout`            | `120`              | Per-request HTTP timeout, seconds                      |
| `max_retries`        | `3`                | SDK retries on transient failures                      |
| `max_tokens`         | `16000`            | Output ceiling per request                             |
| `refusal_fallback`   | `true`             | Route a declined request to a fallback model           |
| `include_reasons`    | `true`             | Ask for and show a reason per item                     |
| `user_agent`         | `myrssfeed (...)`     | `User-Agent` used when fetching feeds                  |

`--api-key` also exists, but prefer the environment variable: flags are visible
in `ps` output and end up in shell history.

### Exit status

| Code | Meaning                                       |
| ---- | --------------------------------------------- |
| `0`  | Ran successfully (whether or not it matched)   |
| `1`  | The run failed                                |
| `2`  | Bad arguments or configuration                |
| `3`  | Ran, but some feeds or batches failed          |

## Library use

The CLI is a thin wrapper; the same thing works from Python:

```python
from myrssfeed import Config, Runner

config = Config(
    system_prompt="Release notes for Python web frameworks.",
    feeds=["https://blog.djangoproject.com/rss/"],
    state_path="~/.local/state/myrssfeed/state.json",
)

result = Runner(config).run_once()
for match in result.matches:
    print(match.item.title, match.item.link, "-", match.verdict.reason)
```

`Runner` accepts a `reader`, a `relevance_filter` and a `state` if you want to
substitute your own — which is how the test suite avoids the network entirely.

## Writing a good prompt

The prompt is the whole filter, so be concrete about both halves:

```
Keep:
  - Postgres release announcements and migration-breaking changes
  - Write-ups of real production incidents with root-cause detail

Skip:
  - Vendor benchmarks and marketing posts
  - "Top 10 database tips" listicles
  - Anything about MySQL unless it compares directly to Postgres
```

The model is told to prefer precision over recall, so tangential items are
dropped. If you are missing things you wanted, say so in the prompt explicitly
("err on the side of including borderline items") or raise `--effort`.

## Development

```bash
pip install -e ".[dev]"
pytest                 # no network or API calls
ruff check . && ruff format --check .
mypy
```

The test suite never reaches the network or the Anthropic API: feeds are parsed
from in-memory XML and the classifier is a scripted fake, so it runs offline and
without an API key.

## Releasing

Publishing uses [trusted publishing](https://docs.pypi.org/trusted-publishers/),
so no API token is stored in this repository. One-time setup, done once per index
at <https://pypi.org/manage/account/publishing/> and the same page on
<https://test.pypi.org>:

| Field       | Value          |
| ----------- | -------------- |
| Project     | `myrssfeed`       |
| Owner       | `fabianhogger` |
| Repository  | `myrssfeed`       |
| Workflow    | `release.yml`  |
| Environment | `pypi` on PyPI, `testpypi` on TestPyPI |

Then, per release:

```bash
# 1. Rehearse on TestPyPI, and check the result installs
gh workflow run release.yml
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ myrssfeed

# 2. Bump __version__ in src/myrssfeed/__init__.py, move the CHANGELOG entry out
#    of [Unreleased] into a version heading, then tag and push
git commit -am "Release 0.1.0"
git tag -a v0.1.0 -m "0.1.0"
git push origin main v0.1.0
```

The tag push is what publishes to the real PyPI. The workflow refuses to upload
if the tag does not match `__version__`, because PyPI never allows a version
number to be reused — a mistake means yanking the release and shipping a new
number, so it is checked before the upload rather than after.

To inspect a build locally first:

```bash
pip install build twine
python -m build
twine check --strict dist/*
```

## License

[MIT](LICENSE).
