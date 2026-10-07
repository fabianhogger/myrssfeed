"""Exception hierarchy for myfeed.

Every error raised deliberately by this package derives from :class:`MyFeedError`,
so callers embedding the library can catch a single base class.
"""

from __future__ import annotations

__all__ = [
    "ClassificationError",
    "ConfigError",
    "FeedError",
    "MyFeedError",
    "StateError",
]


class MyFeedError(Exception):
    """Base class for all errors raised by myfeed."""


class ConfigError(MyFeedError):
    """The supplied configuration is missing a value or is malformed."""


class FeedError(MyFeedError):
    """A feed could not be retrieved or parsed."""

    def __init__(self, url: str, message: str) -> None:
        super().__init__(f"{url}: {message}")
        self.url = url
        self.message = message


class ClassificationError(MyFeedError):
    """The model did not return a usable relevance decision."""


class StateError(MyFeedError):
    """The state file could not be read or written."""
