"""Using myfeed as a library instead of through the CLI.

ANTHROPIC_API_KEY=sk-ant-... python examples/library_usage.py
"""

from __future__ import annotations

from myfeed import Config, Runner

config = Config(
    system_prompt=(
        "Keep release announcements and breaking changes for Python web "
        "frameworks. Skip tutorials, opinion pieces and job postings."
    ),
    feeds=[
        "https://blog.djangoproject.com/rss/",
        "https://flask.palletsprojects.com/en/stable/changes.atom",
    ],
    # Without a state path, every run reconsiders every item in the feed.
    state_path="~/.local/state/myfeed/example-state.json",
    effort="low",
)


def main() -> None:
    result = Runner(config).run_once()
    print(f"{len(result.matches)} of {result.considered} new item(s) matched\n")
    for match in result.matches:
        print(match.item.title)
        print(f"  {match.item.link}")
        print(f"  why: {match.verdict.reason}\n")
    for error in result.feed_errors:
        print(f"feed failed: {error}")


if __name__ == "__main__":
    main()
