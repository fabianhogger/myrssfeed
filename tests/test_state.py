"""The seen-items record and its on-disk format."""

from __future__ import annotations

import json
import time

import pytest

from myrssfeed.errors import StateError
from myrssfeed.state import STATE_VERSION, FeedCursor, State


def test_in_memory_state_never_writes():
    state = State()
    state.mark_seen(["a"])
    state.save()  # must not raise despite there being no path
    assert state.is_seen("a")


def test_round_trip(tmp_path):
    path = tmp_path / "nested" / "state.json"
    state = State(path=path)
    state.mark_seen(["a", "b"])
    state.set_cursor("https://f.example", FeedCursor(etag='"abc"', modified="Mon, 06 Oct 2026"))
    state.save()

    reloaded = State.load(path)
    assert reloaded.is_seen("a") and reloaded.is_seen("b")
    assert reloaded.cursor_for("https://f.example").etag == '"abc"'
    assert reloaded.cursor_for("https://other.example") == FeedCursor()


def test_saved_file_is_versioned_json(tmp_path):
    path = tmp_path / "state.json"
    state = State(path=path)
    state.mark_seen(["a"])
    state.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == STATE_VERSION
    assert "a" in data["seen"]


def test_empty_cursor_is_not_stored(tmp_path):
    state = State(path=tmp_path / "state.json")
    state.set_cursor("https://f.example", FeedCursor())
    assert "https://f.example" not in state.cursors


def test_cursor_is_cleared_when_validators_disappear():
    state = State()
    state.set_cursor("https://f.example", FeedCursor(etag='"v1"'))
    state.set_cursor("https://f.example", FeedCursor())
    assert state.cursor_for("https://f.example") == FeedCursor()


def test_prune_drops_entries_past_the_ttl():
    state = State(ttl_days=1)
    now = time.time()
    state.seen = {"old": now - 2 * 86400, "fresh": now}
    assert state.prune(now=now) == 1
    assert "fresh" in state.seen and "old" not in state.seen


def test_save_prunes_first(tmp_path):
    path = tmp_path / "state.json"
    state = State(path=path, ttl_days=1)
    state.seen = {"old": time.time() - 10 * 86400}
    state.save()
    assert json.loads(path.read_text(encoding="utf-8"))["seen"] == {}


class TestCorruptState:
    """A damaged state file must degrade to "forget everything", never crash."""

    def test_unparsable_file_is_ignored(self, tmp_path, caplog):
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")
        state = State.load(path)
        assert state.seen == {}
        assert "unreadable state file" in caplog.text

    def test_unknown_version_is_ignored(self, tmp_path, caplog):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"version": 999, "seen": {"a": 1}}), encoding="utf-8")
        assert State.load(path).seen == {}
        assert "unsupported version" in caplog.text

    def test_malformed_entries_are_skipped(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps(
                {
                    "version": STATE_VERSION,
                    "seen": {"good": 1.0, "bad": "yesterday"},
                    "cursors": {"https://f.example": "nonsense"},
                }
            ),
            encoding="utf-8",
        )
        state = State.load(path)
        assert state.seen == {"good": 1.0}
        assert state.cursor_for("https://f.example") == FeedCursor()

    def test_missing_file_is_empty_state(self, tmp_path):
        assert State.load(tmp_path / "absent.json").seen == {}


def test_unwritable_path_raises_state_error(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    state = State(path=blocker / "state.json")
    state.mark_seen(["a"])
    with pytest.raises(StateError, match="cannot write state file"):
        state.save()
