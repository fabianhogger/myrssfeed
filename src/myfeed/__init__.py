"""myfeed - read RSS feeds and keep only the news that matches your criteria.

The criteria are a plain-English system prompt; Claude decides, per item,
whether it fits. Matching items are printed with their links.

Typical library use::

    from myfeed import Config, Runner

    config = Config(
        system_prompt="Open-source release notes for Python web frameworks.",
        feeds=["https://example.com/feed.xml"],
    )
    result = Runner(config).run_once()
    for match in result.matches:
        print(match.item.title, match.item.link)
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import Config
from .errors import (
    ClassificationError,
    ConfigError,
    FeedError,
    MyFeedError,
    StateError,
)
from .feeds import FeedReader
from .filter import FilterResult, RelevanceFilter
from .models import FeedItem, Match, Verdict
from .output import render
from .runner import Runner, RunResult
from .state import State

__all__ = [
    "ClassificationError",
    "Config",
    "ConfigError",
    "FeedError",
    "FeedItem",
    "FeedReader",
    "FilterResult",
    "Match",
    "MyFeedError",
    "RelevanceFilter",
    "RunResult",
    "Runner",
    "State",
    "StateError",
    "Verdict",
    "__version__",
    "render",
]
