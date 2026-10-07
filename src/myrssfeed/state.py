"""Persistence of what has already been seen.

Scheduled runs re-read the same feeds repeatedly. Without a record of which
entries were already judged, every run would re-send the whole feed to the API
and re-print the same stories. The state file keeps that from happening and also
stores HTTP validators so unchanged feeds cost a single conditional request.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .errors import StateError

__all__ = ["FeedCursor", "State"]

_LOG = logging.getLogger(__name__)

#: Bumped when the on-disk layout changes incompatibly; an unknown version is
#: discarded rather than guessed at.
STATE_VERSION = 1


@dataclass
class FeedCursor:
    """HTTP cache validators for one feed."""

    etag: Optional[str] = None
    modified: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"etag": self.etag, "modified": self.modified}

    @classmethod
    def from_dict(cls, data: Any) -> FeedCursor:
        if not isinstance(data, dict):
            return cls()
        etag = data.get("etag")
        modified = data.get("modified")
        return cls(
            etag=etag if isinstance(etag, str) else None,
            modified=modified if isinstance(modified, str) else None,
        )


@dataclass
class State:
    """The "already handled" record, loaded from and saved to a JSON file.

    A ``path`` of ``None`` makes the state purely in-memory: useful for one-off
    runs and for tests.
    """

    path: Optional[Path] = None
    ttl_days: int = 30
    seen: Dict[str, float] = field(default_factory=dict)
    cursors: Dict[str, FeedCursor] = field(default_factory=dict)

    # -- loading and saving ----------------------------------------------

    @classmethod
    def load(cls, path: Optional[Path], ttl_days: int = 30) -> State:
        """Read state from ``path``, returning empty state if it is absent.

        A corrupt or future-versioned file is reported and then ignored: losing
        the record means some items are re-printed once, which is far better
        than refusing to run.
        """
        state = cls(path=Path(path).expanduser() if path else None, ttl_days=ttl_days)
        if state.path is None or not state.path.exists():
            return state
        try:
            data = json.loads(state.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            _LOG.warning("ignoring unreadable state file %s: %s", state.path, exc)
            return state
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            _LOG.warning(
                "ignoring state file %s: unsupported version %r",
                state.path,
                data.get("version") if isinstance(data, dict) else None,
            )
            return state
        seen = data.get("seen")
        if isinstance(seen, dict):
            state.seen = {
                str(key): float(value)
                for key, value in seen.items()
                if isinstance(value, (int, float))
            }
        cursors = data.get("cursors")
        if isinstance(cursors, dict):
            state.cursors = {
                str(key): FeedCursor.from_dict(value) for key, value in cursors.items()
            }
        return state

    def save(self) -> None:
        """Write the state atomically. No-op when no path is configured."""
        if self.path is None:
            return
        self.prune()
        payload = {
            "version": STATE_VERSION,
            "seen": self.seen,
            "cursors": {url: cursor.to_dict() for url, cursor in self.cursors.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling temp file and rename, so an interrupted run
            # cannot leave a half-written state file behind.
            handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed below
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.path)
            except BaseException:
                _unlink_quietly(handle.name)
                raise
        except OSError as exc:
            raise StateError(f"cannot write state file {self.path}: {exc}") from exc

    # -- queries and updates ---------------------------------------------

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.seen

    def mark_seen(self, item_ids: Iterable[str], *, now: Optional[float] = None) -> None:
        """Record ``item_ids`` as handled."""
        timestamp = time.time() if now is None else now
        for item_id in item_ids:
            self.seen[item_id] = timestamp

    def cursor_for(self, feed_url: str) -> FeedCursor:
        return self.cursors.get(feed_url, FeedCursor())

    def set_cursor(self, feed_url: str, cursor: FeedCursor) -> None:
        if cursor.etag or cursor.modified:
            self.cursors[feed_url] = cursor
        else:
            self.cursors.pop(feed_url, None)

    def prune(self, *, now: Optional[float] = None) -> int:
        """Drop item ids older than :attr:`ttl_days`; returns how many went.

        Feeds drop old entries themselves, so remembering ids forever would
        grow the file without ever preventing a duplicate.
        """
        cutoff = (time.time() if now is None else now) - self.ttl_days * 86400
        stale = [key for key, seen_at in self.seen.items() if seen_at < cutoff]
        for key in stale:
            del self.seen[key]
        return len(stale)


def _unlink_quietly(path: str) -> None:
    with contextlib.suppress(OSError):  # best-effort cleanup
        os.unlink(path)
