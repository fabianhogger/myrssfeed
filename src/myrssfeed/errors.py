"""Exception hierarchy for myrssfeed.

Every error raised deliberately by this package derives from :class:`MyRssFeedError`,
so callers embedding the library can catch a single base class.
"""

from __future__ import annotations

__all__ = [
    "ClassificationError",
    "ConfigError",
    "FeedError",
    "MyRssFeedError",
    "StateError",
]


class MyRssFeedError(Exception):
    """Base class for all errors raised by myrssfeed."""


class ConfigError(MyRssFeedError):
    """The supplied configuration is missing a value or is malformed."""


class FeedError(MyRssFeedError):
    """A feed could not be retrieved or parsed."""

    def __init__(self, url: str, message: str) -> None:
        super().__init__(f"{url}: {message}")
        self.url = url
        self.message = message


class ClassificationError(MyRssFeedError):
    """The model did not return a usable relevance decision."""


class StateError(MyRssFeedError):
    """The state file could not be read or written."""
