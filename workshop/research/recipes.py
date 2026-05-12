"""Façade for the research-recipes subsystem.

Pure helpers for callers (`persona/teacher/triggers.py`,
`persona/teacher/tools/manifest.py`). Pulls in heavier imports lazily so
the module is cheap to import from anywhere.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import UUID

from workshop.research import recipe_parameterize, recipe_store


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


# ---- host helpers ----------------------------------------------------------


def host_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        h = urlparse(url).hostname
        return h or None
    except Exception:
        return None


def host_from_calls(tool_calls: List[Dict[str, Any]]) -> Optional[str]:
    """Walk a recorded sequence, find the first URL-shaped arg, return
    its host. Used at record time when the URL was set by the LLM (not
    the user) and we need to discover the host post-hoc."""
    for call in tool_calls or []:
        args = call.get("arguments") or {}
        for v in args.values():
            if isinstance(v, str) and _URL_RE.match(v):
                h = host_from_url(v)
                if h:
                    return h
    return None


# ---- canvas auto-detect ---------------------------------------------------


async def infer_url_from_canvas(user_id: UUID) -> Optional[str]:
    """If exactly one `web_view` block is mounted on the user's canvas,
    return its URL. Returns None if zero or 2+ are mounted (ambiguous)
    OR on any error reading the canvas (the caller falls through to the
    fresh research path, which is always safe)."""
    try:
        from workshop.canvas.tools.read_media import read_media
        perc = await read_media(user_id)
    except Exception as e:
        print(f"[recipes.infer_url] read_media failed: {e}", flush=True)
        return None

    candidates: List[str] = []
    for canvas in (perc.canvases or []):
        for block in (canvas.blocks or []):
            state = getattr(block, "state", None)
            if state is None:
                continue
            if getattr(state, "kind", None) != "web_view":
                continue
            extra = getattr(state, "extra", None) or {}
            url = extra.get("url") if isinstance(extra, dict) else None
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                candidates.append(url)

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    # Multiple web_views — ambiguous. Defer to the LLM to disambiguate
    # via the explicit page_url arg.
    return None


# ---- recording -------------------------------------------------------------


async def record_after_success(
    user_id: UUID,
    goal: str,
    tool_calls_seen: List[Dict[str, Any]],
    captured_refs: List[Dict[str, Any]],
) -> Optional[UUID]:
    """Persist a successful Lane R run as a replayable recipe.

    `tool_calls_seen` is the trigger's accumulated list of
    `{name, arguments}` (in source order). `captured_refs` is the
    `refs` list from the FIRST `browser_set(action="snapshot")` made
    during the run.

    Returns the new recipe id, or None on any failure (record path
    must never throw).
    """
    goal = (goal or "").strip()
    if not goal:
        return None

    host = host_from_calls(tool_calls_seen)
    if not host:
        # No URL touched — nothing site-specific to remember.
        return None

    try:
        from infra.rag.embedding import embed_text
        emb = await embed_text(goal)
    except Exception as e:
        print(f"[recipes.record] embed_text failed: {e}", flush=True)
        return None

    if not emb:
        return None

    parameterized = recipe_parameterize.parameterize(
        tool_calls_seen, captured_refs,
    )

    recipe = recipe_store.make_recipe(
        host=host,
        goal_text=goal,
        goal_embedding=list(emb),
        tool_call_sequence=parameterized,
        recorded_refs=list(captured_refs or []),
    )

    try:
        rid = await recipe_store.save(user_id, recipe)
        print(
            f"[recipes.record] saved {rid} host={host} "
            f"steps={len(parameterized)} refs={len(captured_refs or [])} "
            f"goal={goal[:60]!r}",
            flush=True,
        )
        return rid
    except Exception as e:
        print(f"[recipes.record] save failed: {e}", flush=True)
        return None


__all__ = [
    "host_from_url",
    "host_from_calls",
    "infer_url_from_canvas",
    "record_after_success",
]
