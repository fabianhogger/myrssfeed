"""Configuration loading, validation and layering."""

from __future__ import annotations

import json

import pytest

from myfeed.config import Config, parse_interval, read_config_file
from myfeed.errors import ConfigError

FEEDS = ["https://example.com/feed.xml"]


def _require_toml() -> None:
    """Skip when neither tomllib (3.11+) nor the tomli backport is available."""
    try:
        import tomllib  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("tomli", reason="needs tomllib or tomli")


def make_config(**overrides) -> Config:
    defaults = {"system_prompt": "AI policy news", "feeds": list(FEEDS)}
    defaults.update(overrides)
    return Config(**defaults)


class TestValidation:
    def test_minimal_config_is_valid(self):
        config = make_config()
        assert config.model.startswith("claude-")
        assert config.feeds == FEEDS

    def test_blank_prompt_is_rejected(self):
        with pytest.raises(ConfigError, match="system prompt is required"):
            make_config(system_prompt="   \n ")

    def test_missing_feeds_is_rejected(self):
        with pytest.raises(ConfigError, match="at least one feed"):
            make_config(feeds=[])

    def test_unsupported_scheme_is_rejected(self):
        with pytest.raises(ConfigError, match="unsupported feed URL scheme"):
            make_config(feeds=["ftp://example.com/feed.xml"])

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("effort", "turbo", "effort must be one of"),
            ("output_format", "yaml", "output format must be one of"),
            ("batch_size", 0, "batch_size must be at least 1"),
            ("max_items_per_feed", -1, "must not be negative"),
            ("state_ttl_days", 0, "state_ttl_days must be at least 1"),
            ("max_tokens", 10, "max_tokens must be at least"),
            ("timeout", 0, "timeout must be positive"),
            ("max_retries", -1, "max_retries must not be negative"),
            ("interval", 0, "interval must be positive"),
        ],
    )
    def test_out_of_range_values_are_rejected(self, field, value, message):
        with pytest.raises(ConfigError, match=message):
            make_config(**{field: value})

    def test_feeds_are_deduped_and_stripped(self):
        config = make_config(
            feeds=["  https://a.example/f  ", "https://a.example/f", "https://b.example/f"]
        )
        assert config.feeds == ["https://a.example/f", "https://b.example/f"]

    def test_state_path_string_becomes_a_path(self, tmp_path):
        config = make_config(state_path=str(tmp_path / "state.json"))
        assert config.state_path == tmp_path / "state.json"

    def test_replace_revalidates(self):
        config = make_config()
        assert config.replace(effort="high").effort == "high"
        with pytest.raises(ConfigError):
            config.replace(effort="nope")


class TestParseInterval:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [("45", 45), ("30s", 30), ("5m", 300), ("2h", 7200), ("1d", 86400), ("1h30m", 5400)],
    )
    def test_valid_intervals(self, text, seconds):
        assert parse_interval(text) == seconds

    @pytest.mark.parametrize("text", ["", "   ", "m", "10x", "5m3", "0", "0s"])
    def test_invalid_intervals(self, text):
        with pytest.raises(ConfigError):
            parse_interval(text)


class TestFromMapping:
    def test_unknown_keys_are_reported(self):
        with pytest.raises(ConfigError, match="unknown configuration key"):
            Config.from_mapping({"system_prompt": "x", "feeds": FEEDS, "colour": "blue"})

    def test_missing_prompt_is_reported(self):
        with pytest.raises(ConfigError, match="missing 'system_prompt'"):
            Config.from_mapping({"feeds": FEEDS})

    def test_string_interval_is_parsed(self):
        config = Config.from_mapping({"system_prompt": "x", "feeds": FEEDS, "interval": "15m"})
        assert config.interval == 900


class TestFiles:
    def test_json_config_round_trip(self, tmp_path):
        path = tmp_path / "myfeed.json"
        path.write_text(
            json.dumps({"system_prompt": "security advisories", "feeds": FEEDS}),
            encoding="utf-8",
        )
        config = Config.from_file(path)
        assert config.system_prompt == "security advisories"

    def test_toml_config_round_trip(self, tmp_path):
        _require_toml()
        path = tmp_path / "myfeed.toml"
        path.write_text(
            'system_prompt = "security advisories"\n'
            'feeds = ["https://example.com/feed.xml"]\n'
            'interval = "20m"\n',
            encoding="utf-8",
        )
        config = Config.from_file(path)
        assert config.interval == 1200

    def test_missing_file_is_reported(self, tmp_path):
        with pytest.raises(ConfigError, match="cannot read config file"):
            read_config_file(tmp_path / "absent.json")

    def test_invalid_json_is_reported(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="invalid JSON"):
            read_config_file(path)

    def test_non_object_top_level_is_reported(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(ConfigError, match="table/object"):
            read_config_file(path)


class TestEnvOverrides:
    def test_values_are_coerced_by_field_type(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("MYFEED_FEEDS", "https://a.example/f, https://b.example/f")
        monkeypatch.setenv("MYFEED_BATCH_SIZE", "4")
        monkeypatch.setenv("MYFEED_TIMEOUT", "30.5")
        monkeypatch.setenv("MYFEED_INCLUDE_REASONS", "no")
        monkeypatch.setenv("MYFEED_INTERVAL", "10m")
        overrides = Config.env_overrides()
        assert overrides["api_key"] == "sk-test"
        assert overrides["feeds"] == ["https://a.example/f", "https://b.example/f"]
        assert overrides["batch_size"] == 4
        assert overrides["timeout"] == 30.5
        assert overrides["include_reasons"] is False
        assert overrides["interval"] == 600

    def test_empty_values_are_ignored(self, monkeypatch):
        monkeypatch.setenv("MYFEED_MODEL", "")
        assert "model" not in Config.env_overrides()

    def test_bad_boolean_is_reported(self, monkeypatch):
        monkeypatch.setenv("MYFEED_INCLUDE_REASONS", "perhaps")
        with pytest.raises(ConfigError, match="expected a boolean"):
            Config.env_overrides()

    def test_bad_number_is_reported(self, monkeypatch):
        monkeypatch.setenv("MYFEED_BATCH_SIZE", "many")
        with pytest.raises(ConfigError, match="expected a number"):
            Config.env_overrides()
