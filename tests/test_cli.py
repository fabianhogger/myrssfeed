"""The command-line interface: argument handling, layering and exit codes."""

from __future__ import annotations

import io
import json

import pytest

from conftest import FakeFilter, FakeReader
from myrssfeed import cli
from myrssfeed.errors import FeedError
from myrssfeed.state import State

PROMPT = ["--prompt", "AI policy news"]
FEED = ["--feed", "https://example.com/feed.xml"]


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Keep the developer's own environment out of the CLI tests."""
    for name in ("ANTHROPIC_API_KEY", "MYRSSFEED_MODEL", "MYRSSFEED_FEEDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


@pytest.fixture
def fake_runner(monkeypatch, items):
    """Replace the Runner's collaborators so no network or API is touched."""
    created = {}
    original_init = cli.Runner.__init__

    def patched_init(self, config, **kwargs):
        kwargs.setdefault(
            "reader",
            FakeReader(created.get("items", items), created.get("errors", ())),
        )
        kwargs.setdefault("relevance_filter", FakeFilter(created.get("relevant", ("a",))))
        kwargs.setdefault("state", State())
        original_init(self, config, **kwargs)
        created["config"] = config
        created["runner"] = self

    monkeypatch.setattr(cli.Runner, "__init__", patched_init)
    return created


def run(argv, stream=None):
    out = stream if stream is not None else io.StringIO()
    code = cli.main(argv, stream=out)
    return code, out.getvalue()


class TestBasicRuns:
    def test_a_matching_item_is_printed(self, fake_runner):
        code, out = run(PROMPT + FEED)
        assert code == cli.EXIT_OK
        assert "Chip export rules tightened again" in out
        assert "https://example.com/chips" in out

    def test_json_output(self, fake_runner):
        code, out = run(PROMPT + FEED + ["--format", "json"])
        assert code == cli.EXIT_OK
        assert json.loads(out)[0]["id"] == "a"

    def test_feed_failure_reports_partial_success(self, fake_runner):
        fake_runner["errors"] = (FeedError("https://bad.example/f", "HTTP 500"),)
        code, _ = run(PROMPT + FEED)
        assert code == cli.EXIT_PARTIAL

    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert "myrssfeed" in capsys.readouterr().out


class TestConfigLayering:
    def test_flags_beat_the_config_file(self, tmp_path, fake_runner):
        path = tmp_path / "c.json"
        path.write_text(
            json.dumps(
                {
                    "system_prompt": "from file",
                    "feeds": ["https://file.example/f"],
                    "model": "claude-sonnet-5-5",
                }
            ),
            encoding="utf-8",
        )
        code, _ = run(["--config", str(path), "--model", "claude-opus-5-5"])
        assert code == cli.EXIT_OK
        config = fake_runner["config"]
        assert config.system_prompt == "from file"
        assert config.feeds == ["https://file.example/f"]
        assert config.model == "claude-opus-5-5"

    def test_env_beats_the_config_file(self, tmp_path, fake_runner, monkeypatch):
        path = tmp_path / "c.json"
        path.write_text(
            json.dumps(
                {"system_prompt": "p", "feeds": ["https://f.example/f"], "effort": "low"}
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("MYRSSFEED_EFFORT", "high")
        run(["--config", str(path)])
        assert fake_runner["config"].effort == "high"

    def test_prompt_from_a_file(self, tmp_path, fake_runner):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("security advisories only", encoding="utf-8")
        run(FEED + ["--prompt-file", str(prompt)])
        assert fake_runner["config"].system_prompt == "security advisories only"

    def test_prompt_from_stdin(self, fake_runner, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
        run(FEED + ["--prompt-file", "-"])
        assert fake_runner["config"].system_prompt == "from stdin"

    def test_feeds_from_a_file_with_comments(self, tmp_path, fake_runner):
        feeds = tmp_path / "feeds.txt"
        feeds.write_text(
            "# my feeds\nhttps://a.example/f\n\nhttps://b.example/f  # inline\n",
            encoding="utf-8",
        )
        run(PROMPT + ["--feeds-file", str(feeds)])
        assert fake_runner["config"].feeds == ["https://a.example/f", "https://b.example/f"]

    def test_repeated_feed_flags_accumulate(self, fake_runner):
        run(PROMPT + ["--feed", "https://a.example/f", "--feed", "https://b.example/f"])
        assert len(fake_runner["config"].feeds) == 2

    def test_interval_is_parsed(self, tmp_path, fake_runner, monkeypatch):
        # --once must win over a configured interval so the test does not loop.
        run(PROMPT + FEED + ["--interval", "15m", "--once"])
        assert fake_runner["config"].interval is None

    def test_no_state_disables_persistence(self, fake_runner, tmp_path):
        run(PROMPT + FEED + ["--state", str(tmp_path / "s.json"), "--no-state"])
        assert fake_runner["config"].state_path is None

    def test_toggles_are_applied(self, fake_runner):
        run(PROMPT + FEED + ["--no-reasons", "--no-refusal-fallback"])
        config = fake_runner["config"]
        assert config.include_reasons is False
        assert config.refusal_fallback is False


class TestScheduling:
    def test_an_interval_starts_the_loop(self, fake_runner, monkeypatch):
        calls = {"n": 0}

        def fake_forever(self, stream, **kwargs):
            calls["n"] += 1
            return 1

        monkeypatch.setattr(cli.Runner, "run_forever", fake_forever)
        code, _ = run(PROMPT + FEED + ["--interval", "30m"])
        assert code == cli.EXIT_OK
        assert calls["n"] == 1

    def test_keyboard_interrupt_exits_cleanly(self, fake_runner, monkeypatch):
        def boom(self):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli.Runner, "run_once", boom)
        assert run(PROMPT + FEED)[0] == cli.EXIT_OK


class TestDryRun:
    def test_items_are_listed_without_calling_the_model(self, monkeypatch, items):
        monkeypatch.setattr(cli, "FeedReader", lambda *a, **k: FakeReader(items))

        def fail(*_a, **_k):  # pragma: no cover - must not be reached
            raise AssertionError("the model must not be called during a dry run")

        monkeypatch.setattr(cli, "Runner", fail)
        code, out = run(PROMPT + FEED + ["--dry-run"])
        assert code == cli.EXIT_OK
        assert "Local bakery wins award" in out

    def test_dry_run_does_not_mark_items_as_seen(self, monkeypatch, items, tmp_path):
        state_path = tmp_path / "state.json"
        monkeypatch.setattr(cli, "FeedReader", lambda *a, **k: FakeReader(items))
        run(PROMPT + FEED + ["--dry-run", "--state", str(state_path)])
        assert not state_path.exists()

    def test_feed_errors_make_a_dry_run_partial(self, monkeypatch, items):
        monkeypatch.setattr(
            cli,
            "FeedReader",
            lambda *a, **k: FakeReader(items, (FeedError("https://b.example/f", "HTTP 500"),)),
        )
        assert run(PROMPT + FEED + ["--dry-run"])[0] == cli.EXIT_PARTIAL


class TestUsageErrors:
    def _expect_usage(self, argv, message, capsys):
        """A configuration problem returns EXIT_USAGE and explains itself."""
        assert cli.main(argv) == cli.EXIT_USAGE
        assert message in capsys.readouterr().err

    def test_missing_prompt(self, capsys):
        self._expect_usage(FEED, "no system prompt", capsys)

    def test_missing_feeds(self, capsys):
        self._expect_usage(PROMPT, "no feeds", capsys)

    def test_prompt_and_prompt_file_conflict(self, capsys):
        self._expect_usage(PROMPT + FEED + ["--prompt-file", "x.txt"], "not both", capsys)

    def test_unreadable_feeds_file(self, capsys, tmp_path):
        self._expect_usage(
            PROMPT + ["--feeds-file", str(tmp_path / "absent.txt")], "cannot read", capsys
        )

    def test_empty_feeds_file(self, capsys, tmp_path):
        path = tmp_path / "feeds.txt"
        path.write_text("# only a comment\n", encoding="utf-8")
        self._expect_usage(PROMPT + ["--feeds-file", str(path)], "no feed URLs", capsys)

    def test_bad_interval(self, capsys):
        self._expect_usage(PROMPT + FEED + ["--interval", "soon"], "invalid interval", capsys)

    def test_invalid_choice_is_rejected_by_argparse(self, capsys):
        # Malformed flags are argparse's business, and it exits rather than
        # returning; the status still matches EXIT_USAGE.
        with pytest.raises(SystemExit) as excinfo:
            cli.main([*PROMPT, *FEED, "--effort", "turbo"])
        assert excinfo.value.code == cli.EXIT_USAGE


def test_parser_builds_and_documents_exit_codes():
    help_text = cli.build_parser().format_help()
    assert "exit status" in help_text
    assert "ANTHROPIC_API_KEY" in help_text
