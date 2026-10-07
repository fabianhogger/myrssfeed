"""Feed item rendering and identity."""

from __future__ import annotations

from datetime import datetime, timezone

from myrssfeed.models import SUMMARY_CHAR_LIMIT, FeedItem, Match, Verdict, stable_item_id


def test_prompt_block_contains_the_fields_the_model_needs(items):
    block = items[0].to_prompt_block(3)
    assert 'index="3"' in block
    assert "title: Chip export rules tightened again" in block
    assert "source: Example Wire" in block
    assert "published: 2026-10-06T10:00:00+00:00" in block
    # The link is deliberately withheld: the model must judge from the text.
    assert "https://example.com/chips" not in block


def test_prompt_block_omits_empty_fields():
    item = FeedItem(item_id="x", title="Bare", link="", feed_url="https://f.example")
    block = item.to_prompt_block(0)
    assert "source:" not in block
    assert "published:" not in block
    assert "summary:" not in block


def test_long_summaries_are_truncated():
    item = FeedItem(
        item_id="x",
        title="Long",
        link="",
        feed_url="https://f.example",
        summary="word " * 2000,
    )
    assert len(item.to_prompt_block(0)) < SUMMARY_CHAR_LIMIT + 200


def test_published_iso_is_normalised_to_utc():
    item = FeedItem(
        item_id="x",
        title="t",
        link="",
        feed_url="https://f.example",
        published=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert item.published_iso == "2026-01-01T12:00:00+00:00"


def test_items_are_hashable_by_id():
    first = FeedItem(item_id="same", title="a", link="", feed_url="f")
    second = FeedItem(item_id="same", title="b", link="", feed_url="f")
    assert len({first, second}) == 2  # distinct values...
    assert hash(first) == hash(second)  # ...but one identity bucket


def test_match_to_dict_carries_the_reason(items):
    match = Match(item=items[0], verdict=Verdict(relevant=True, reason="on topic"))
    assert match.to_dict()["reason"] == "on topic"
    assert match.to_dict()["link"] == "https://example.com/chips"


class TestStableItemId:
    def test_guid_wins(self):
        assert stable_item_id(guid="urn:1", link="https://x", title="t") == "urn:1"

    def test_link_is_the_fallback(self):
        assert stable_item_id(link=" https://x ", title="t") == "https://x"

    def test_hash_is_the_last_resort_and_is_deterministic(self):
        first = stable_item_id(title="t", published="2026-01-01")
        assert first.startswith("sha256:")
        assert first == stable_item_id(title="t", published="2026-01-01")
        assert first != stable_item_id(title="t", published="2026-01-02")


def test_short_summary_caps_verbose_feeds():
    item = FeedItem(
        item_id="x",
        title="Long",
        link="",
        feed_url="https://f.example",
        summary="word " * 2000,
    )
    assert len(item.short_summary) <= SUMMARY_CHAR_LIMIT
    assert item.short_summary.endswith("...")
    # to_dict reports the same text the model was given.
    assert item.to_dict()["summary"] == item.short_summary


def test_short_summary_of_an_empty_summary_is_empty():
    item = FeedItem(item_id="x", title="t", link="", feed_url="f")
    assert item.short_summary == ""
