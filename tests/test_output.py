"""Rendering of matched items."""

from __future__ import annotations

import json

import pytest

from myfeed.models import FeedItem, Match, Verdict
from myfeed.output import render


@pytest.fixture
def matches(items):
    return [
        Match(item=items[0], verdict=Verdict(relevant=True, reason="export controls")),
        Match(item=items[2], verdict=Verdict(relevant=True, reason="internal memo")),
    ]


class TestText:
    def test_links_and_reasons_are_shown(self, matches):
        out = render(matches, "text")
        assert "Chip export rules tightened again" in out
        assert "https://example.com/chips" in out
        assert "why: export controls" in out
        assert out.endswith("\n")

    def test_reasons_can_be_hidden(self, matches):
        assert "why:" not in render(matches, "text", show_reasons=False)

    def test_empty_result_says_so(self):
        assert render([], "text") == "No matching items."


class TestMarkdown:
    def test_titles_are_linked(self, matches):
        out = render(matches, "markdown")
        assert "- [Chip export rules tightened again](https://example.com/chips)" in out

    def test_markdown_characters_are_escaped(self):
        item = FeedItem(
            item_id="x",
            title="Release [1.0] *final*",
            link="https://example.com/r",
            feed_url="https://example.com/feed",
        )
        out = render([Match(item=item, verdict=Verdict(True))], "markdown")
        assert r"\[1.0\]" in out and r"\*final\*" in out

    def test_item_without_a_link_is_still_listed(self):
        item = FeedItem(item_id="x", title="No link", link="", feed_url="f")
        assert render([Match(item=item, verdict=Verdict(True))], "markdown") == "- No link\n"

    def test_empty_result_says_so(self):
        assert render([], "markdown") == "_No matching items._\n"


class TestJson:
    def test_output_is_a_parsable_array(self, matches):
        payload = json.loads(render(matches, "json"))
        assert [entry["id"] for entry in payload] == ["a", "c"]
        assert payload[0]["link"] == "https://example.com/chips"
        assert payload[0]["reason"] == "export controls"
        assert payload[0]["published"] == "2026-10-06T10:00:00+00:00"

    def test_undated_items_report_a_null_date(self, matches):
        assert json.loads(render(matches, "json"))[1]["published"] is None

    def test_reasons_can_be_hidden(self, matches):
        assert "reason" not in json.loads(render(matches, "json", show_reasons=False))[0]

    def test_empty_result_is_an_empty_array(self):
        assert json.loads(render([], "json")) == []


def test_unknown_format_is_rejected(matches):
    with pytest.raises(ValueError, match="unknown output format"):
        render(matches, "yaml")
