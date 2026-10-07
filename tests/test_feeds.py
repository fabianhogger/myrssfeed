"""Feed parsing and normalisation."""

from __future__ import annotations

from datetime import timezone

import pytest

from myrssfeed.errors import FeedError
from myrssfeed.feeds import FeedReader, parse_feed
from myrssfeed.state import FeedCursor, State

pytest.importorskip("feedparser")


class TestParsing:
    def test_entries_become_items(self, rss_xml):
        items = parse_feed(rss_xml, "https://example.com/feed.xml")
        assert [item.title for item in items] == [
            "Chip export rules tightened again",
            "Local bakery wins award",
            "Undated memo",
        ]
        first = items[0]
        assert first.item_id == "urn:example:chips"
        assert first.link == "https://example.com/chips"
        assert first.feed_title == "Example Wire"
        assert first.published.tzinfo is timezone.utc

    def test_html_is_stripped_from_summaries(self, rss_xml):
        first = parse_feed(rss_xml, "https://example.com/feed.xml")[0]
        assert first.summary == "New controls on accelerators."

    def test_categories_are_collected(self, rss_xml):
        assert parse_feed(rss_xml, "https://example.com/feed.xml")[0].tags == ("policy",)

    def test_undated_items_sort_last(self, rss_xml):
        items = parse_feed(rss_xml, "https://example.com/feed.xml")
        assert items[-1].title == "Undated memo"

    def test_max_items_keeps_the_newest(self, rss_xml):
        items = parse_feed(rss_xml, "https://example.com/feed.xml", max_items=1)
        assert [item.title for item in items] == ["Chip export rules tightened again"]

    def test_out_of_order_feed_is_sorted(self):
        xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
        <item><title>Older</title><guid>1</guid>
          <pubDate>Mon, 05 Oct 2026 00:00:00 GMT</pubDate></item>
        <item><title>Newer</title><guid>2</guid>
          <pubDate>Tue, 06 Oct 2026 00:00:00 GMT</pubDate></item>
        </channel></rss>"""
        assert [item.title for item in parse_feed(xml)] == ["Newer", "Older"]


class TestFeedReader:
    """``read_one`` is driven through a stubbed ``feedparser.parse``."""

    @staticmethod
    def _stub(monkeypatch, result, recorder=None):
        import feedparser

        def fake_parse(url, **kwargs):
            if recorder is not None:
                recorder.append((url, kwargs))
            return result

        monkeypatch.setattr(feedparser, "parse", fake_parse)

    def test_new_items_are_returned_and_seen_ones_skipped(self, monkeypatch, rss_xml):
        import feedparser

        parsed = feedparser.parse(rss_xml)
        parsed["status"] = 200
        self._stub(monkeypatch, parsed)
        state = State()
        state.mark_seen(["urn:example:chips"])
        reader = FeedReader(state)
        titles = [item.title for item in reader.read_one("https://example.com/feed.xml")]
        assert "Chip export rules tightened again" not in titles
        assert "Local bakery wins award" in titles

    def test_validators_are_sent_and_stored(self, monkeypatch, rss_xml):
        import feedparser

        parsed = feedparser.parse(rss_xml)
        parsed["status"] = 200
        parsed["etag"] = '"v2"'
        calls = []
        self._stub(monkeypatch, parsed, calls)
        state = State()
        state.set_cursor("https://example.com/feed.xml", FeedCursor(etag='"v1"'))
        FeedReader(state).read_one("https://example.com/feed.xml")
        assert calls[0][1]["etag"] == '"v1"'
        assert state.cursor_for("https://example.com/feed.xml").etag == '"v2"'

    def test_not_modified_short_circuits(self, monkeypatch):
        self._stub(monkeypatch, {"status": 304, "entries": [], "feed": {}})
        assert FeedReader(State()).read_one("https://example.com/feed.xml") == []

    def test_http_error_raises(self, monkeypatch):
        self._stub(monkeypatch, {"status": 404, "entries": [], "feed": {}})
        with pytest.raises(FeedError, match="HTTP 404"):
            FeedReader(State()).read_one("https://example.com/feed.xml")

    def test_unparsable_body_raises(self, monkeypatch):
        self._stub(
            monkeypatch,
            {
                "status": 200,
                "entries": [],
                "feed": {},
                "bozo": 1,
                "bozo_exception": ValueError("not xml"),
            },
        )
        with pytest.raises(FeedError, match="could not parse feed"):
            FeedReader(State()).read_one("https://example.com/feed.xml")

    def test_empty_but_valid_feed_is_not_an_error(self, monkeypatch):
        self._stub(monkeypatch, {"status": 200, "entries": [], "feed": {"title": "Quiet"}})
        assert FeedReader(State()).read_one("https://example.com/feed.xml") == []

    def test_user_agent_is_sent(self, monkeypatch, rss_xml):
        import feedparser

        parsed = feedparser.parse(rss_xml)
        parsed["status"] = 200
        calls = []
        self._stub(monkeypatch, parsed, calls)
        FeedReader(State(), user_agent="myrssfeed/test").read_one(
            "https://example.com/feed.xml"
        )
        assert calls[0][1]["agent"] == "myrssfeed/test"

    def test_read_collects_errors_without_aborting(self, monkeypatch, rss_xml):
        import feedparser

        good = feedparser.parse(rss_xml)
        good["status"] = 200

        def fake_parse(url, **kwargs):
            if "bad" in url:
                return {"status": 500, "entries": [], "feed": {}}
            return good

        monkeypatch.setattr(feedparser, "parse", fake_parse)
        items, errors = FeedReader(State()).read(
            ["https://bad.example/feed", "https://good.example/feed"]
        )
        assert items, "the healthy feed should still be read"
        assert len(errors) == 1
        assert errors[0].url == "https://bad.example/feed"

    def test_duplicate_items_across_feeds_are_collapsed(self, monkeypatch, rss_xml):
        import feedparser

        parsed = feedparser.parse(rss_xml)
        parsed["status"] = 200
        self._stub(monkeypatch, parsed)
        items, _ = FeedReader(State()).read(
            ["https://a.example/feed", "https://b.example/feed"]
        )
        assert len({item.item_id for item in items}) == len(items)
