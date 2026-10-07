# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-07

Initial release.

### Added

- Filter RSS/Atom feeds against a plain-English system prompt, judged by Claude,
  printing matching items with their links and a one-line reason.
- `myfeed` command line interface with layered configuration: TOML/JSON config
  file, `MYFEED_*` environment variables, then flags.
- Scheduling: a built-in `--interval` loop with clean `SIGINT`/`SIGTERM`
  shutdown, or single `--once` passes for cron and systemd timers.
- State file recording handled items, so repeated runs only report new ones;
  items from a failed batch are retried on the next run.
- Conditional feed requests using stored `ETag`/`Last-Modified` validators.
- `text`, `markdown` and `json` output formats.
- `--dry-run` to inspect what would be judged without calling the API.
- Public library API: `Config`, `Runner`, `FeedReader`, `RelevanceFilter`.

[Unreleased]: https://github.com/fabianhogger/myfeed/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/fabianhogger/myfeed/releases/tag/v0.1.0
