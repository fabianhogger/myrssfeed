"""Relevance filtering: request shape, response parsing and failure handling."""

from __future__ import annotations

import json

import pytest

from conftest import FakeClient, FakeResponse
from myrssfeed.errors import ClassificationError
from myrssfeed.filter import RelevanceFilter


def decisions(*pairs) -> str:
    return json.dumps(
        {
            "decisions": [
                {"index": index, "relevant": relevant, "reason": "because"}
                for index, relevant in pairs
            ]
        }
    )


def make_filter(client, **kwargs) -> RelevanceFilter:
    options = {"system_prompt": "AI policy news", "client": client}
    options.update(kwargs)
    return RelevanceFilter(**options)


class TestRequestShape:
    def test_criteria_and_schema_are_sent(self, items):
        client = FakeClient(FakeResponse(decisions((0, True), (1, False), (2, False))))
        make_filter(client).select(items)
        request = client.messages.requests[0]
        assert request["model"].startswith("claude-")
        system = request["system"][0]["text"]
        assert "AI policy news" in system
        assert "<reader_criteria>" in system
        assert system.index("You are a news filter") < system.index("AI policy news")
        schema = request["output_config"]["format"]["schema"]
        assert schema["properties"]["decisions"]["items"]["required"] == [
            "index",
            "relevant",
            "reason",
        ]

    def test_system_prompt_is_cached(self, items):
        client = FakeClient(FakeResponse(decisions((0, True), (1, False), (2, False))))
        make_filter(client).select(items)
        assert client.messages.requests[0]["system"][0]["cache_control"] == {
            "type": "ephemeral"
        }

    def test_effort_is_passed_through(self, items):
        client = FakeClient(FakeResponse(decisions((0, True), (1, False), (2, False))))
        make_filter(client, effort="high").select(items)
        assert client.messages.requests[0]["output_config"]["effort"] == "high"

    def test_refusal_fallback_is_on_by_default(self, items):
        client = FakeClient(FakeResponse(decisions((0, True), (1, False), (2, False))))
        make_filter(client).select(items)
        request = client.messages.requests[0]
        assert request["fallbacks"] == "default"
        assert request["betas"] == ["server-side-fallback-2026-07-01"]

    def test_refusal_fallback_can_be_disabled(self, items):
        client = FakeClient(FakeResponse(decisions((0, True), (1, False), (2, False))))
        make_filter(client, refusal_fallback=False).select(items)
        assert "fallbacks" not in client.messages.requests[0]

    def test_reasons_can_be_dropped_from_the_schema(self, items):
        client = FakeClient(FakeResponse(decisions((0, True), (1, False), (2, False))))
        make_filter(client, include_reasons=False).select(items)
        schema = client.messages.requests[0]["output_config"]["format"]["schema"]
        assert schema["properties"]["decisions"]["items"]["required"] == [
            "index",
            "relevant",
        ]

    def test_every_item_appears_in_the_prompt(self, items):
        client = FakeClient(FakeResponse(decisions((0, True), (1, False), (2, False))))
        make_filter(client).select(items)
        content = client.messages.requests[0]["messages"][0]["content"]
        for item in items:
            assert item.title in content
        assert "Return one decision for each of the 3 articles" in content


class TestBatching:
    def test_items_are_split_into_batches(self, items):
        client = FakeClient(
            FakeResponse(decisions((0, True), (1, False))),
            FakeResponse(decisions((0, True))),
        )
        result = make_filter(client, batch_size=2).select(items)
        assert len(client.messages.requests) == 2
        assert [match.item.item_id for match in result.matches] == ["a", "c"]
        assert result.judged_ids == {"a", "b", "c"}

    def test_a_failed_batch_does_not_lose_the_others(self, items):
        client = FakeClient(
            FakeResponse("", stop_reason="refusal"),
            FakeResponse(decisions((0, True))),
        )
        result = make_filter(client, batch_size=2).select(items)
        assert result.failed_batches == 1
        # The failed batch's items stay unjudged so the next run retries them.
        assert result.judged_ids == {"c"}
        assert [match.item.item_id for match in result.matches] == ["c"]

    def test_empty_input_makes_no_requests(self):
        client = FakeClient()
        result = make_filter(client).select([])
        assert result.matches == [] and not client.messages.requests


class TestResponseParsing:
    def test_verdicts_are_matched_by_index_not_order(self, items):
        client = FakeClient(FakeResponse(decisions((2, True), (0, False), (1, False))))
        result = make_filter(client).select(items)
        assert [match.item.item_id for match in result.matches] == ["c"]

    def test_reason_is_kept(self, items):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {"decisions": [{"index": 0, "relevant": True, "reason": " on topic "}]}
                )
            )
        )
        result = make_filter(client, batch_size=1).select(items[:1])
        assert result.matches[0].verdict.reason == "on topic"

    def test_a_missing_verdict_counts_as_no_match(self, items, caplog):
        client = FakeClient(FakeResponse(decisions((0, True))))
        result = make_filter(client).select(items)
        assert [match.item.item_id for match in result.matches] == ["a"]
        assert result.judged_ids == {"a", "b", "c"}
        assert "no verdict returned" in caplog.text

    def test_malformed_decisions_are_skipped(self, items):
        payload = json.dumps(
            {
                "decisions": [
                    "nonsense",
                    {"index": "zero", "relevant": True},
                    {"index": 1, "relevant": "yes"},
                    {"index": 0, "relevant": True},
                ]
            }
        )
        client = FakeClient(FakeResponse(payload))
        result = make_filter(client).select(items)
        assert [match.item.item_id for match in result.matches] == ["a"]


class TestErrors:
    def _expect(self, response, items, message):
        flt = make_filter(FakeClient(response))
        with pytest.raises(ClassificationError, match=message):
            flt.judge_batch(items)

    def test_refusal(self, items):
        self._expect(FakeResponse("", stop_reason="refusal"), items, "declined to classify")

    def test_refusal_includes_the_category(self, items):
        details = type("Details", (), {"category": "cyber"})()
        self._expect(
            FakeResponse("", stop_reason="refusal", stop_details=details),
            items,
            "category: cyber",
        )

    def test_truncated_response(self, items):
        self._expect(
            FakeResponse(decisions((0, True)), stop_reason="max_tokens"),
            items,
            "hit max_tokens",
        )

    def test_no_text_block(self, items):
        self._expect(FakeResponse(""), items, "no text block")

    def test_invalid_json(self, items):
        self._expect(FakeResponse("{oops"), items, "not valid JSON")

    def test_missing_decisions_array(self, items):
        self._expect(FakeResponse(json.dumps({"verdicts": []})), items, "no 'decisions'")

    def test_no_usable_decisions(self, items):
        self._expect(
            FakeResponse(json.dumps({"decisions": ["junk"]})), items, "no usable decisions"
        )

    def test_transport_failure_is_wrapped(self, items):
        class Boom:
            class messages:  # mimics the SDK's attribute layout
                @staticmethod
                def create(**_kwargs):
                    raise RuntimeError("connection reset")

            beta = type("Beta", (), {"messages": messages})()

        flt = make_filter(Boom())
        with pytest.raises(ClassificationError, match="connection reset"):
            flt.judge_batch(items)

    def test_empty_prompt_is_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            RelevanceFilter("   ")

    def test_empty_batch_short_circuits(self):
        assert make_filter(FakeClient()).judge_batch([]) == []
