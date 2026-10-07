"""Fetching and normalising RSS/Atom feeds.

``feedparser`` does the parsing; this module's job is to turn its loosely typed
output into :class:`~myfeed.models.FeedItem` objects, to apply conditional GETs,
and to make sure one broken feed cannot abort a whole run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from .errors import FeedError
from .models import FeedItem, stable_item_id
from .state import FeedCursor, State

__all__ = ["FeedReader", "parse_feed"]

_LOG = logging.getLogger(__name__)

#: ``304 Not Modified`` means the feed is unchanged since our last visit.
_NOT_MODIFIED = 304


class FeedReader:
    """Reads feeds, newest entries first, skipping ones already handled.

    Args:
        state: Record of seen items and HTTP validators. Pass an empty
            :class:`~myfeed.state.State` to reconsider everything.
        max_items_per_feed: Keep at most this many of the newest entries per
            feed; ``0`` keeps all of them.
        user_agent: ``User-Agent`` header sent with each request.
    """

    def __init__(
        self,
        state: Optional[State] = None,
        *,
        max_items_per_feed: int = 50,
        user_agent: str = "myfeed",
    ) -> None:
        self._state = state if state is not None else State()
        self._max_items = max_items_per_feed
        self._user_agent = user_agent

    def read(self, feed_urls: Sequence[str]) -> Tuple[List[FeedItem], List[FeedError]]:
        """Read every feed in ``feed_urls``.

        Returns the collected new items and the errors for feeds that could not
        be read. Errors are returned rather than raised so that a single dead
        feed does not hide the news from the others.
        """
        items: List[FeedItem] = []
        errors: List[FeedError] = []
        seen_ids = set()
        for url in feed_urls:
            try:
                fetched = self.read_one(url)
            except FeedError as exc:
                _LOG.warning("skipping feed %s", exc)
                errors.append(exc)
                continue
            for item in fetched:
                # The same story can appear in several feeds; keep the first.
                if item.item_id in seen_ids:
                    continue
                seen_ids.add(item.item_id)
                items.append(item)
        return items, errors

    def read_one(self, feed_url: str) -> List[FeedItem]:
        """Read a single feed and return its unseen entries, newest first.

        Raises:
            FeedError: if the feed cannot be retrieved or contains no parsable
                channel.
        """
        import feedparser  # imported lazily so `--help` works without it

        cursor = self._state.cursor_for(feed_url)
        parsed = feedparser.parse(
            feed_url,
            etag=cursor.etag,
            modified=cursor.modified,
            agent=self._user_agent,
        )
        status = _field(parsed, "status")
        if status == _NOT_MODIFIED:
            _LOG.debug("%s unchanged (304)", feed_url)
            return []
        if isinstance(status, int) and status >= 400:
            raise FeedError(feed_url, f"HTTP {status}")

        entries = _field(parsed, "entries") or []
        if not entries:
            # A bozo flag with no entries means the body was not a feed at all;
            # a bozo flag *with* entries is usually a recoverable quirk.
            exception = _field(parsed, "bozo_exception")
            if exception is not None:
                raise FeedError(feed_url, f"could not parse feed: {exception}")
            _LOG.debug("%s has no entries", feed_url)
            return []
        if _field(parsed, "bozo"):
            _LOG.debug(
                "%s parsed with warnings: %s", feed_url, _field(parsed, "bozo_exception", "")
            )

        self._state.set_cursor(
            feed_url,
            FeedCursor(
                etag=_field(parsed, "etag"),
                modified=_field(parsed, "modified"),
            ),
        )

        feed_title = _text(_field(parsed, "feed", {}), "title")
        items = [_to_item(entry, feed_url, feed_title) for entry in entries]
        items = _newest_first(items)
        fresh = [item for item in items if not self._state.is_seen(item.item_id)]
        if self._max_items:
            fresh = fresh[: self._max_items]
        _LOG.info("%s: %d entries, %d new", feed_url, len(items), len(fresh))
        return fresh


def parse_feed(
    content: str, feed_url: str = "memory://feed", *, max_items: int = 0
) -> List[FeedItem]:
    """Parse feed XML already held in memory. Primarily useful for tests."""
    import feedparser

    parsed = feedparser.parse(content)
    entries = _field(parsed, "entries") or []
    feed_title = _text(_field(parsed, "feed", {}), "title")
    items = _newest_first([_to_item(entry, feed_url, feed_title) for entry in entries])
    return items[:max_items] if max_items else items


def _field(source: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a parse result.

    ``feedparser`` returns a dict subclass that also allows attribute access;
    both are accepted here so the reader works with plain mappings too.
    """
    if hasattr(source, "get"):
        value = source.get(key, default)
        if value is not None:
            return value
    return getattr(source, key, default)


def _newest_first(items: Iterable[FeedItem]) -> List[FeedItem]:
    """Sort by publication date, newest first, with undated items last.

    Many feeds are already ordered this way, but enough are not that relying on
    document order would silently truncate the wrong items under
    ``max_items_per_feed``.
    """
    ordered = list(items)
    ordered.sort(
        key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return ordered


def _to_item(entry: Any, feed_url: str, feed_title: str) -> FeedItem:
    title = _text(entry, "title") or "(untitled)"
    link = _text(entry, "link")
    published = _timestamp(entry)
    item_id = stable_item_id(
        guid=_text(entry, "id"),
        link=link,
        title=title,
        published=published.isoformat() if published else "",
    )
    return FeedItem(
        item_id=item_id,
        title=title,
        link=link,
        feed_url=feed_url,
        feed_title=feed_title,
        summary=_summary(entry),
        author=_text(entry, "author"),
        published=published,
        tags=_tags(entry),
    )


def _text(source: Any, key: str) -> str:
    value = source.get(key) if hasattr(source, "get") else None
    return value.strip() if isinstance(value, str) else ""


def _summary(entry: Any) -> str:
    """Pick the richest available description and strip it down to text."""
    for key in ("summary", "description"):
        text = _text(entry, key)
        if text:
            return _strip_html(text)
    contents = entry.get("content") if hasattr(entry, "get") else None
    if isinstance(contents, list):
        for block in contents:
            value = block.get("value") if hasattr(block, "get") else None
            if isinstance(value, str) and value.strip():
                return _strip_html(value)
    return ""


def _tags(entry: Any) -> tuple:
    tags = entry.get("tags") if hasattr(entry, "get") else None
    if not isinstance(tags, list):
        return ()
    labels = []
    for tag in tags:
        term = tag.get("term") if hasattr(tag, "get") else None
        if isinstance(term, str) and term.strip():
            labels.append(term.strip())
    return tuple(labels)


def _timestamp(entry: Any) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key) if hasattr(entry, "get") else None
        if value is None:
            continue
        try:
            # feedparser normalises these struct_time values to UTC.
            year, month, day, hour, minute, second = value[:6]
            return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        except (TypeError, ValueError):  # pragma: no cover - malformed date
            continue
    return None


def _strip_html(value: str) -> str:
    """Flatten HTML markup to plain text.

    Summaries are prompt input, not display output: tags add tokens without
    adding information the model needs.
    """
    from html import unescape
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: List[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

    stripper = _Stripper()
    try:
        stripper.feed(value)
        stripper.close()
        text = "".join(stripper.parts)
    except Exception:  # pragma: no cover - malformed markup
        text = value
    return " ".join(unescape(text).split())
