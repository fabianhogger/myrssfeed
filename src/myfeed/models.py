"""Core value objects: feed items, model verdicts and the pairing of the two."""

from __future__ import annotations

import hashlib
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

__all__ = ["FeedItem", "Verdict", "Match"]

#: Summaries are truncated before being sent to the model. Entire articles are
#: rarely needed to judge relevance, and the cap keeps per-item token cost flat
#: regardless of how verbose a particular feed is.
SUMMARY_CHAR_LIMIT = 1200


@dataclass(frozen=True)
class FeedItem:
    """A single entry read from an RSS or Atom feed.

    Instances are hashable and compare by :attr:`item_id`, which is the stable
    identity used for deduplication across runs.
    """

    item_id: str
    title: str
    link: str
    feed_url: str
    feed_title: str = ""
    summary: str = ""
    author: str = ""
    published: Optional[datetime] = None
    tags: tuple = ()

    def __hash__(self) -> int:  # pragma: no cover - trivial
        return hash(self.item_id)

    @property
    def published_iso(self) -> str:
        """The publication timestamp as an ISO 8601 string, or ``""``."""
        if self.published is None:
            return ""
        return self.published.astimezone(timezone.utc).isoformat()

    def to_prompt_block(self, index: int) -> str:
        """Render the item as the numbered block the classifier reads.

        ``index`` is the position within the batch; the model echoes it back so
        verdicts can be matched to items without relying on ordering.
        """
        lines = [f"<item index=\"{index}\">", f"title: {self.title}"]
        if self.feed_title:
            lines.append(f"source: {self.feed_title}")
        if self.author:
            lines.append(f"author: {self.author}")
        if self.published_iso:
            lines.append(f"published: {self.published_iso}")
        if self.tags:
            lines.append("categories: " + ", ".join(self.tags))
        if self.summary:
            summary = textwrap.shorten(
                self.summary, width=SUMMARY_CHAR_LIMIT, placeholder=" ..."
            )
            lines.append(f"summary: {summary}")
        lines.append("</item>")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """A JSON-serialisable representation of the item."""
        return {
            "id": self.item_id,
            "title": self.title,
            "link": self.link,
            "feed_url": self.feed_url,
            "feed_title": self.feed_title,
            "author": self.author,
            "published": self.published_iso or None,
            "tags": list(self.tags),
            "summary": self.summary,
        }


@dataclass(frozen=True)
class Verdict:
    """The model's relevance judgement for one item."""

    relevant: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"relevant": self.relevant, "reason": self.reason}


@dataclass(frozen=True)
class Match:
    """A feed item together with the verdict that was reached for it."""

    item: FeedItem
    verdict: Verdict

    def to_dict(self) -> Dict[str, Any]:
        payload = self.item.to_dict()
        payload["reason"] = self.verdict.reason
        return payload


def stable_item_id(
    *,
    guid: str = "",
    link: str = "",
    title: str = "",
    published: str = "",
) -> str:
    """Derive a deterministic identity for a feed entry.

    Feeds are inconsistent about identifiers: ``guid`` is preferred, then the
    permalink, and only as a last resort a hash of the title and date. The
    fallback keeps items distinguishable in feeds that publish neither a guid
    nor stable links.
    """
    for candidate in (guid, link):
        if candidate and candidate.strip():
            return candidate.strip()
    digest = hashlib.sha256(f"{title}\x00{published}".encode("utf-8")).hexdigest()
    return f"sha256:{digest[:32]}"
