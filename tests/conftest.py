"""Shared fixtures and fakes.

Nothing in the test suite touches the network or the Anthropic API: feeds are
parsed from in-memory XML and the classifier is replaced by a scripted fake.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Sequence

import pytest

from myfeed.filter import FilterResult
from myfeed.models import FeedItem, Match, Verdict


@pytest.fixture
def rss_xml() -> str:
    """A small, well-formed RSS 2.0 feed with three dated entries."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Wire</title>
    <link>https://example.com/</link>
    <description>Test feed</description>
    <item>
      <title>Chip export rules tightened again</title>
      <link>https://example.com/chips</link>
      <guid>urn:example:chips</guid>
      <pubDate>Tue, 06 Oct 2026 10:00:00 GMT</pubDate>
      <description>
        &lt;p&gt;New &lt;b&gt;controls&lt;/b&gt; on accelerators.&lt;/p&gt;
      </description>
      <category>policy</category>
    </item>
    <item>
      <title>Local bakery wins award</title>
      <link>https://example.com/bakery</link>
      <guid>urn:example:bakery</guid>
      <pubDate>Mon, 05 Oct 2026 08:30:00 GMT</pubDate>
      <description>Sourdough, mostly.</description>
    </item>
    <item>
      <title>Undated memo</title>
      <link>https://example.com/memo</link>
      <guid>urn:example:memo</guid>
      <description>No date on this one.</description>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def items() -> List[FeedItem]:
    """Three feed items, newest first."""
    return [
        FeedItem(
            item_id="a",
            title="Chip export rules tightened again",
            link="https://example.com/chips",
            feed_url="https://example.com/feed.xml",
            feed_title="Example Wire",
            summary="New controls on advanced accelerators.",
            published=datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc),
        ),
        FeedItem(
            item_id="b",
            title="Local bakery wins award",
            link="https://example.com/bakery",
            feed_url="https://example.com/feed.xml",
            feed_title="Example Wire",
            summary="Sourdough, mostly.",
            published=datetime(2026, 10, 5, 8, 30, tzinfo=timezone.utc),
        ),
        FeedItem(
            item_id="c",
            title="Undated memo",
            link="https://example.com/memo",
            feed_url="https://example.com/feed.xml",
            feed_title="Example Wire",
            summary="No date on this one.",
        ),
    ]


class FakeFilter:
    """A classifier that matches items whose id is in ``relevant_ids``."""

    def __init__(self, relevant_ids: Sequence[str], *, fail_ids: Sequence[str] = ()) -> None:
        self.relevant_ids = set(relevant_ids)
        self.fail_ids = set(fail_ids)
        self.calls: List[List[str]] = []

    def select(self, items: Sequence[FeedItem]) -> FilterResult:
        self.calls.append([item.item_id for item in items])
        result = FilterResult()
        for item in items:
            if item.item_id in self.fail_ids:
                result.failed_batches += 1
                continue
            result.judged_ids.add(item.item_id)
            if item.item_id in self.relevant_ids:
                result.matches.append(
                    Match(item=item, verdict=Verdict(relevant=True, reason="matches"))
                )
        return result


class FakeReader:
    """A feed reader that returns canned items and records what it was asked."""

    def __init__(self, items: Sequence[FeedItem], errors: Sequence[object] = ()) -> None:
        self._items = list(items)
        self._errors = list(errors)
        self.requested: List[Sequence[str]] = []

    def read(self, feed_urls):
        self.requested.append(list(feed_urls))
        return list(self._items), list(self._errors)


class FakeResponse:
    """Stands in for an ``anthropic`` Message."""

    def __init__(self, text: str = "", *, stop_reason: str = "end_turn", stop_details=None):
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.content = [_TextBlock(text)] if text else []


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeMessages:
    """Captures requests and replays queued responses."""

    def __init__(self, responses: Sequence[FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: List[Dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("no more scripted responses")
        return self._responses.pop(0)


class FakeClient:
    """Minimal stand-in exposing both ``messages`` and ``beta.messages``."""

    def __init__(self, *responses: FakeResponse) -> None:
        self.messages = FakeMessages(responses)
        self.beta = type("Beta", (), {"messages": self.messages})()
