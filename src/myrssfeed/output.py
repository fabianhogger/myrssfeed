"""Rendering matched items for the terminal or for downstream tools."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from .models import Match

__all__ = ["render", "render_json", "render_markdown", "render_text"]


def render(matches: Sequence[Match], output_format: str, *, show_reasons: bool = True) -> str:
    """Render ``matches`` in the requested format.

    Raises:
        ValueError: if ``output_format`` is not recognised.
    """
    renderers = {
        "text": render_text,
        "markdown": render_markdown,
        "json": render_json,
    }
    try:
        renderer = renderers[output_format]
    except KeyError:
        raise ValueError(f"unknown output format: {output_format!r}") from None
    return renderer(matches, show_reasons=show_reasons)


def render_text(matches: Sequence[Match], *, show_reasons: bool = True) -> str:
    """Plain text, one block per item: title, link, then the reason."""
    if not matches:
        return "No matching items."
    lines: List[str] = []
    for match in matches:
        item = match.item
        lines.append(item.title)
        if item.link:
            lines.append(f"  {item.link}")
        meta = " · ".join(part for part in (item.feed_title, item.published_iso) if part)
        if meta:
            lines.append(f"  {meta}")
        if show_reasons and match.verdict.reason:
            lines.append(f"  why: {match.verdict.reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(matches: Sequence[Match], *, show_reasons: bool = True) -> str:
    """Markdown list with linked titles, suitable for pasting into a note."""
    if not matches:
        return "_No matching items._\n"
    lines: List[str] = []
    for match in matches:
        item = match.item
        title = _escape(item.title)
        lines.append(f"- [{title}]({item.link})" if item.link else f"- {title}")
        meta = " · ".join(part for part in (item.feed_title, item.published_iso) if part)
        if meta:
            lines.append(f"  - {_escape(meta)}")
        if show_reasons and match.verdict.reason:
            lines.append(f"  - {_escape(match.verdict.reason)}")
    return "\n".join(lines) + "\n"


def render_json(matches: Sequence[Match], *, show_reasons: bool = True) -> str:
    """A JSON array of matched items, for piping into other tools."""
    payload: List[Dict[str, Any]] = []
    for match in matches:
        entry = match.item.to_dict()
        if show_reasons:
            entry["reason"] = match.verdict.reason
        payload.append(entry)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _escape(value: str) -> str:
    """Escape the Markdown characters that break link text and list items."""
    for char in ("\\", "[", "]", "*", "_", "`"):
        value = value.replace(char, "\\" + char)
    return value
