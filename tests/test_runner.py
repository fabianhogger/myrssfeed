"""Orchestration: a single pass and the scheduled loop."""

from __future__ import annotations

import io

import pytest

from conftest import FakeFilter, FakeReader
from myrssfeed.config import Config
from myrssfeed.errors import FeedError
from myrssfeed.runner import Runner
from myrssfeed.state import State


def make_runner(items, *, relevant=("a",), fail_ids=(), errors=(), state=None, **config_kwargs):
    options = {
        "system_prompt": "AI policy news",
        "feeds": ["https://example.com/feed.xml"],
    }
    options.update(config_kwargs)
    config = Config(**options)
    reader = FakeReader(items, errors)
    flt = FakeFilter(relevant, fail_ids=fail_ids)
    runner = Runner(
        config,
        reader=reader,
        relevance_filter=flt,
        state=state if state is not None else State(),
    )
    return runner, reader, flt


class TestRunOnce:
    def test_matching_items_are_returned(self, items):
        runner, _, _ = make_runner(items, relevant=("a", "c"))
        result = runner.run_once()
        assert [match.item.item_id for match in result.matches] == ["a", "c"]
        assert result.considered == 3
        assert result.ok

    def test_judged_items_are_recorded_as_seen(self, items):
        runner, _, _ = make_runner(items)
        runner.run_once()
        assert all(runner.state.is_seen(item.item_id) for item in items)

    def test_unjudged_items_are_not_recorded(self, items):
        runner, _, _ = make_runner(items, fail_ids=("b",))
        result = runner.run_once()
        assert not runner.state.is_seen("b"), "a failed item must be retried next run"
        assert result.failed_batches == 1
        assert not result.ok

    def test_feed_errors_are_reported_but_not_fatal(self, items):
        error = FeedError("https://bad.example/feed", "HTTP 500")
        runner, _, _ = make_runner(items, errors=(error,))
        result = runner.run_once()
        assert result.feed_errors == [error]
        assert result.matches, "healthy feeds should still produce matches"
        assert not result.ok

    def test_no_new_items_skips_the_model(self, items):
        runner, _, flt = make_runner([])
        result = runner.run_once()
        assert result.considered == 0
        assert flt.calls == []
        assert result.ok

    def test_state_is_persisted_between_runs(self, items, tmp_path):
        path = tmp_path / "state.json"
        runner, _, _ = make_runner(items, state=State(path=path))
        runner.run_once()
        assert State.load(path).is_seen("a")

    def test_state_write_failure_does_not_fail_the_run(self, items, tmp_path, caplog):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        runner, _, _ = make_runner(items, state=State(path=blocker / "state.json"))
        result = runner.run_once()
        assert result.matches
        assert "cannot write state file" in caplog.text


class TestReport:
    def test_report_uses_the_configured_format(self, items):
        runner, _, _ = make_runner(items, output_format="json")
        buffer = io.StringIO()
        runner.report(runner.run_once(), buffer)
        assert buffer.getvalue().lstrip().startswith("[")

    def test_report_honours_include_reasons(self, items):
        runner, _, _ = make_runner(items, include_reasons=False)
        buffer = io.StringIO()
        runner.report(runner.run_once(), buffer)
        assert "why:" not in buffer.getvalue()


class TestSchedule:
    def test_runs_repeat_and_sleep_between_passes(self, items):
        runner, _, flt = make_runner(items, interval=60)
        slept = []
        runs = runner.run_forever(io.StringIO(), sleeper=slept.append, max_runs=3)
        assert runs == 3
        assert len(flt.calls) == 3
        # Two sleeps for three runs: the loop exits instead of sleeping last.
        assert len(slept) == 2
        assert all(0 < delay <= 60 for delay in slept)

    def test_a_failing_pass_does_not_stop_the_loop(self, items, caplog):
        runner, reader, _ = make_runner(items, interval=60)

        calls = {"n": 0}
        original = reader.read

        def flaky(feed_urls):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return original(feed_urls)

        reader.read = flaky
        runs = runner.run_forever(io.StringIO(), sleeper=lambda _d: None, max_runs=2)
        assert runs == 2
        assert "run failed" in caplog.text

    def test_a_signal_stops_the_loop_after_the_current_pass(self, items):
        runner, _, flt = make_runner(items, interval=60)
        original = runner.run_once
        stop_flags = []

        def run_and_request_stop():
            result = original()
            # Simulate SIGTERM arriving mid-pass.
            for frame in stop_flags:
                frame.request(15, None)
            return result

        runner.run_once = run_and_request_stop

        import myrssfeed.runner as runner_module

        real_scope_enter = runner_module._SignalScope.__enter__

        def capture(self):
            flag = real_scope_enter(self)
            stop_flags.append(flag)
            return flag

        runner_module._SignalScope.__enter__ = capture
        try:
            runs = runner.run_forever(io.StringIO(), sleeper=lambda _d: None)
        finally:
            runner_module._SignalScope.__enter__ = real_scope_enter
        assert runs == 1
        assert len(flt.calls) == 1

    def test_interval_is_required(self, items):
        runner, _, _ = make_runner(items)
        with pytest.raises(ValueError, match="needs an interval"):
            runner.run_forever(io.StringIO())
