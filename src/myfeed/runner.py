"""Orchestration: one pass over the feeds, or a scheduled loop of passes."""

from __future__ import annotations

import contextlib
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional, TextIO

from .config import Config
from .errors import FeedError, StateError
from .feeds import FeedReader
from .filter import RelevanceFilter
from .models import Match
from .output import render
from .state import State

__all__ = ["RunResult", "Runner"]

_LOG = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Outcome of a single pass over the configured feeds."""

    matches: List[Match] = field(default_factory=list)
    considered: int = 0
    feed_errors: List[FeedError] = field(default_factory=list)
    failed_batches: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def ok(self) -> bool:
        """True when nothing went wrong, whether or not anything matched."""
        return not self.feed_errors and self.failed_batches == 0


class Runner:
    """Runs the read → judge → report cycle.

    Args:
        config: Validated configuration.
        reader: Feed reader; built from ``config`` when omitted.
        relevance_filter: Classifier; built from ``config`` when omitted.
            Passing one in is how tests avoid network access.
        state: State record; loaded from ``config.state_path`` when omitted.
    """

    def __init__(
        self,
        config: Config,
        *,
        reader: Optional[FeedReader] = None,
        relevance_filter: Optional[RelevanceFilter] = None,
        state: Optional[State] = None,
    ) -> None:
        self.config = config
        self.state = (
            state
            if state is not None
            else State.load(config.state_path, ttl_days=config.state_ttl_days)
        )
        self.reader = reader or FeedReader(
            self.state,
            max_items_per_feed=config.max_items_per_feed,
            user_agent=config.user_agent,
        )
        self.filter = relevance_filter or RelevanceFilter(
            config.system_prompt,
            api_key=config.api_key,
            model=config.model,
            effort=config.effort,
            batch_size=config.batch_size,
            max_tokens=config.max_tokens,
            include_reasons=config.include_reasons,
            refusal_fallback=config.refusal_fallback,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    def run_once(self) -> RunResult:
        """Read the feeds, classify what is new, and record what was handled."""
        result = RunResult()
        items, result.feed_errors = self.reader.read(self.config.feeds)
        result.considered = len(items)
        if not items:
            _LOG.info("no new items")
            self._persist(set())
            return result

        _LOG.info("judging %d new item(s)", len(items))
        filtered = self.filter.select(items)
        result.matches = filtered.matches
        result.failed_batches = filtered.failed_batches
        self._persist(filtered.judged_ids)
        _LOG.info("%d of %d item(s) matched", len(result.matches), len(items))
        return result

    def report(self, result: RunResult, stream: TextIO) -> None:
        """Write ``result``'s matches to ``stream`` in the configured format."""
        stream.write(
            render(
                result.matches,
                self.config.output_format,
                show_reasons=self.config.include_reasons,
            )
        )
        stream.flush()

    def run_forever(
        self,
        stream: TextIO,
        *,
        interval: Optional[int] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        max_runs: Optional[int] = None,
    ) -> int:
        """Run on a fixed interval until interrupted.

        Returns the number of passes completed. ``SIGINT``/``SIGTERM`` end the
        loop after the current pass so state is always saved cleanly. A failing
        pass is logged and the loop continues: a feed outage should not take the
        scheduler down.

        Args:
            stream: Where reports are written.
            interval: Seconds between passes; defaults to ``config.interval``.
            sleeper: Injection point for tests; defaults to :func:`time.sleep`.
            max_runs: Stop after this many passes. Mainly for tests.
        """
        delay = interval if interval is not None else self.config.interval
        if not delay:
            raise ValueError("run_forever needs an interval")
        sleep = sleeper or time.sleep
        stop = _StopFlag()
        with stop.installed():
            runs = 0
            while not stop.requested:
                started = time.monotonic()
                try:
                    result = self.run_once()
                    self.report(result, stream)
                except Exception:
                    _LOG.exception("run failed; retrying at the next interval")
                runs += 1
                if max_runs is not None and runs >= max_runs:
                    break
                remaining = delay - (time.monotonic() - started)
                if remaining > 0 and not stop.requested:
                    _LOG.info("sleeping %.0fs until the next run", remaining)
                    sleep(remaining)
            return runs

    def _persist(self, judged_ids: set) -> None:
        self.state.mark_seen(judged_ids)
        try:
            self.state.save()
        except StateError as exc:
            # Losing the record means some items may repeat next run, which is
            # not worth failing an otherwise successful pass over.
            _LOG.error("%s", exc)


class _StopFlag:
    """Tracks a shutdown request from ``SIGINT``/``SIGTERM``."""

    def __init__(self) -> None:
        self.requested = False

    def installed(self) -> _SignalScope:
        return _SignalScope(self)

    def request(self, signum: int, _frame: object) -> None:
        _LOG.info("received signal %s; stopping after this run", signum)
        self.requested = True


class _SignalScope:
    """Installs the stop handlers for the duration of a ``with`` block."""

    _SIGNALS = (signal.SIGINT, signal.SIGTERM)

    def __init__(self, flag: _StopFlag) -> None:
        self._flag = flag
        self._previous: dict = {}

    def __enter__(self) -> _StopFlag:
        for signum in self._SIGNALS:
            try:
                self._previous[signum] = signal.signal(signum, self._flag.request)
            except ValueError:
                # Signal handlers can only be installed on the main thread;
                # off the main thread the loop simply is not interruptible.
                _LOG.debug("cannot install handler for signal %s", signum)
        return self._flag

    def __exit__(self, *_exc: object) -> None:
        for signum, handler in self._previous.items():
            # See __enter__: off the main thread there is nothing to restore.
            with contextlib.suppress(ValueError):
                signal.signal(signum, handler)
