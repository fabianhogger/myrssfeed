"""Configuration loading and validation.

A :class:`Config` can be built in three ways, which the CLI layers in order of
increasing precedence: a config file, environment variables, then explicit
command-line flags.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .errors import ConfigError

__all__ = [
    "DEFAULT_EFFORT",
    "DEFAULT_MODEL",
    "EFFORT_LEVELS",
    "OUTPUT_FORMATS",
    "Config",
    "parse_interval",
    "read_config_file",
]

#: Claude Opus 5.5 is the default: relevance judgements against a free-form
#: prompt benefit from a capable model, and ``effort`` keeps the cost down.
DEFAULT_MODEL = "claude-opus-5-5"

#: Deciding whether a headline matches a stated interest is a short
#: classification task, so the cheapest effort level is the sensible default.
DEFAULT_EFFORT = "low"

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
OUTPUT_FORMATS = ("text", "markdown", "json")

_ENV_PREFIX = "MYFEED_"
_API_KEY_ENV = "ANTHROPIC_API_KEY"

_INTERVAL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(value: str) -> int:
    """Parse a human interval such as ``"30m"`` or ``"1h30m"`` into seconds.

    A bare number is read as seconds. Raises :class:`ConfigError` for anything
    that does not describe a positive duration.
    """
    text = str(value).strip().lower()
    if not text:
        raise ConfigError("interval must not be empty")
    if text.isdigit():
        seconds = int(text)
    else:
        seconds = 0
        number = ""
        for char in text:
            if char.isdigit():
                number += char
            elif char in _INTERVAL_UNITS:
                if not number:
                    raise ConfigError(f"invalid interval {value!r}: missing amount")
                seconds += int(number) * _INTERVAL_UNITS[char]
                number = ""
            else:
                raise ConfigError(f"invalid interval {value!r}: unexpected {char!r}")
        if number:
            raise ConfigError(f"invalid interval {value!r}: missing unit")
    if seconds <= 0:
        raise ConfigError(f"invalid interval {value!r}: must be positive")
    return seconds


@dataclass
class Config:
    """Everything a run needs.

    Attributes:
        system_prompt: The user's criteria, passed to Claude as the system
            prompt. This is the only description of what "relevant" means.
        api_key: Anthropic API key. If ``None`` the SDK resolves credentials
            from the environment, which is the recommended setup.
        feeds: RSS/Atom feed URLs to read.
        model: Claude model id.
        effort: Reasoning effort, one of :data:`EFFORT_LEVELS`.
        batch_size: How many items are judged per API request.
        max_items_per_feed: Cap on newest entries considered per feed, applied
            before classification. ``0`` means no cap.
        state_path: Where the "already seen" record lives. ``None`` disables
            persistence, so every run reconsiders every item.
        state_ttl_days: How long item ids are remembered.
        output_format: One of :data:`OUTPUT_FORMATS`.
        interval: Seconds between runs in scheduled mode; ``None`` runs once.
        timeout: Per-request HTTP timeout in seconds.
        max_retries: SDK-level retry count for transient failures.
        max_tokens: Output token ceiling per classification request.
        refusal_fallback: Enable server-side fallback routing so a declined
            request is retried on another model instead of failing.
        include_reasons: Ask the model to justify each verdict and show it.
        user_agent: ``User-Agent`` sent when fetching feeds.
    """

    system_prompt: str
    feeds: List[str] = field(default_factory=list)
    api_key: Optional[str] = None
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    batch_size: int = 10
    max_items_per_feed: int = 50
    state_path: Optional[Path] = None
    state_ttl_days: int = 30
    output_format: str = "text"
    interval: Optional[int] = None
    timeout: float = 120.0
    max_retries: int = 3
    max_tokens: int = 16000
    refusal_fallback: bool = True
    include_reasons: bool = True
    user_agent: str = "myfeed (+https://github.com/)"

    def __post_init__(self) -> None:
        self.system_prompt = (self.system_prompt or "").strip()
        self.feeds = _dedupe(self.feeds)
        if self.state_path is not None:
            # Accept a plain string too: config files and flags both supply one.
            self.state_path = Path(self.state_path).expanduser()
        self.validate()

    def validate(self) -> None:
        """Raise :class:`ConfigError` if the configuration cannot be run."""
        if not self.system_prompt:
            raise ConfigError(
                "a system prompt is required; it tells the model which news to keep"
            )
        if not self.feeds:
            raise ConfigError("at least one feed URL is required")
        for url in self.feeds:
            if not url.startswith(("http://", "https://", "file://")):
                raise ConfigError(f"unsupported feed URL scheme: {url!r}")
        if self.effort not in EFFORT_LEVELS:
            raise ConfigError(
                f"effort must be one of {', '.join(EFFORT_LEVELS)}, got {self.effort!r}"
            )
        if self.output_format not in OUTPUT_FORMATS:
            raise ConfigError(
                f"output format must be one of {', '.join(OUTPUT_FORMATS)}, "
                f"got {self.output_format!r}"
            )
        if self.batch_size < 1:
            raise ConfigError("batch_size must be at least 1")
        if self.max_items_per_feed < 0:
            raise ConfigError("max_items_per_feed must not be negative")
        if self.state_ttl_days < 1:
            raise ConfigError("state_ttl_days must be at least 1")
        if self.max_tokens < 1024:
            raise ConfigError("max_tokens must be at least 1024")
        if self.timeout <= 0:
            raise ConfigError("timeout must be positive")
        if self.max_retries < 0:
            raise ConfigError("max_retries must not be negative")
        if self.interval is not None and self.interval <= 0:
            raise ConfigError("interval must be positive")

    def replace(self, **changes: Any) -> Config:
        """Return a copy with ``changes`` applied, re-validated."""
        return replace(self, **changes)

    # -- constructors ----------------------------------------------------

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Config:
        """Build a config from a plain mapping, rejecting unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(
                f"unknown configuration key(s): {', '.join(unknown)}; "
                f"supported keys are {', '.join(sorted(known))}"
            )
        payload = dict(data)
        if "system_prompt_file" in data:  # pragma: no cover - defensive
            raise ConfigError("use 'system_prompt' in config files")
        if isinstance(payload.get("interval"), str):
            payload["interval"] = parse_interval(payload["interval"])
        if "system_prompt" not in payload:
            raise ConfigError("configuration is missing 'system_prompt'")
        return cls(**payload)

    @classmethod
    def from_file(cls, path: Path) -> Config:
        """Load and validate a config from a TOML or JSON file."""
        return cls.from_mapping(read_config_file(path))

    @classmethod
    def env_overrides(cls) -> Dict[str, Any]:
        """Collect configuration from the environment.

        Recognised variables are ``ANTHROPIC_API_KEY`` plus ``MYFEED_*`` named
        after the dataclass fields (for example ``MYFEED_MODEL``,
        ``MYFEED_BATCH_SIZE``, ``MYFEED_FEEDS`` as a comma-separated list).
        """
        overrides: Dict[str, Any] = {}
        api_key = os.environ.get(_API_KEY_ENV)
        if api_key:
            overrides["api_key"] = api_key
        for name, spec in cls.__dataclass_fields__.items():
            value = os.environ.get(_ENV_PREFIX + name.upper())
            if value is None or value == "":
                continue
            overrides[name] = _coerce_env(name, value, spec.type)
        return overrides


def read_config_file(path: Path) -> Dict[str, Any]:
    """Parse a TOML or JSON config file into a mapping, without validating it.

    The format is chosen by suffix: ``.json`` is read as JSON, anything else as
    TOML. Validation is left to :meth:`Config.from_mapping`, so the result can
    serve as one layer under environment variables and command-line flags.
    """
    path = Path(path).expanduser()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    else:
        data = _load_toml(path, raw)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a table/object at the top level")
    if isinstance(data.get("interval"), str):
        data["interval"] = parse_interval(data["interval"])
    return data


def _dedupe(values: Iterable[str]) -> List[str]:
    """Strip, drop blanks and de-duplicate while preserving order."""
    seen = set()
    result: List[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _load_toml(path: Path, raw: bytes) -> Any:
    reader: Any
    try:
        # mypy is pinned to the lowest supported Python (3.9), where tomllib
        # does not exist; on 3.11+ at runtime this is the fast path.
        import tomllib  # type: ignore[import-not-found]

        reader = tomllib
    except ModuleNotFoundError:  # Python < 3.11
        try:
            import tomli
        except ModuleNotFoundError as exc:
            # tomli is a dependency on Python < 3.11, so reaching this means
            # the install is incomplete rather than the config being wrong.
            raise ConfigError(
                f"cannot read TOML config {path}: no TOML parser available. "
                "Reinstall myfeed (it depends on 'tomli' below Python 3.11), "
                "or use a .json config file instead"
            ) from exc
        reader = tomli
    try:
        return reader.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, reader.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def _coerce_env(name: str, value: str, type_hint: Any) -> Any:
    """Convert an environment string to the type the field expects."""
    hint = str(type_hint)
    if name == "feeds":
        return list(_split_list(value))
    if name == "interval":
        return parse_interval(value)
    if "bool" in hint:
        return _parse_bool(name, value)
    if "float" in hint:
        return _parse_number(name, value, float)
    if "int" in hint:
        return _parse_number(name, value, int)
    return value


def _split_list(value: str) -> Sequence[str]:
    separator = "," if "," in value else None
    return [part.strip() for part in value.split(separator) if part.strip()]


def _parse_bool(name: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name}: expected a boolean, got {value!r}")


def _parse_number(name: str, value: str, caster: Any) -> Any:
    try:
        return caster(value)
    except ValueError as exc:
        raise ConfigError(f"{name}: expected a number, got {value!r}") from exc
