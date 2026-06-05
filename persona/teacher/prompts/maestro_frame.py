"""Render a Maestro cache entry as a prompt section (PR-5).

The section sits at the TOP of the system prompt — both because it's
the engagement's frame (the LLM should read it first) and because it's
the same prefix across many turns (cache-friendly).

Empty when the cache has no entry for this (user, persona_purpose);
calls that get None should NOT prepend a blank section.
"""
from __future__ import annotations

from typing import Optional


def render(entry: Optional[dict]) -> str:
    """Turn an entry dict (from maestro `/api/maestro/cache`) into the
    text the system prompt should carry. Returns '' when entry is None
    or missing required fields — callers can `+=` unconditionally."""
    if not entry:
        return ""
    paragraph = (entry.get("paragraph") or "").strip()
    posture = (entry.get("posture") or "").strip() or "steady"
    if not paragraph:
        return ""
    lines = [
        "=== ACTIVE MAESTRO FRAME ===",
        f"posture: {posture}",
        "",
        paragraph,
        "=== END ACTIVE MAESTRO FRAME ===",
    ]
    return "\n".join(lines)
