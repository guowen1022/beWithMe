"""Server-side cache of the current rendered HTML for each mounted note.

Phase 2 of voice-leads: the canvas writer needs to see the full content
of an existing note so it can decide between mounting fresh,
appending to it, highlighting parts of it, or revising specific text.

Phase 2.5: the cache now keeps the markdown source alongside the
rendered HTML. The writer's next-turn prompt sees the *markdown* —
cleaner for the LLM to read and mutate — while the client still
receives final HTML for rendering.

  (user_id, block_id) → {"md": <source markdown>, "html": <final HTML>}

`md` is the authoring surface; `html` is what the client actually
shows. `edit_note` ops operate on `md`, then re-render to `html`.
Legacy HTML-only mounts (e.g. via the test path) populate `html` with
no `md`; readers should fall back gracefully.

Updated whenever we know either side changed:
  * `mount_template` (note path) — after sanitize + diagram inline
  * `push_block_content` (note content topic) — after preprocessing
  * `edit_note` — after applying ops and re-rendering markdown

In-process only; mounts are already ephemeral, so a workshop restart
consistently drops both the mounts and this cache.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
from uuid import UUID


@dataclass
class CardEntry:
    md: Optional[str] = None
    html: Optional[str] = None


_cache: dict[Tuple[str, str], CardEntry] = {}


def _key(user_id: UUID, block_id: str) -> Tuple[str, str]:
    return (str(user_id), block_id)


def set(  # noqa: A001 — keep the old name; callers grep `set`
    user_id: UUID,
    block_id: str,
    html: Optional[str] = None,
    md: Optional[str] = None,
) -> None:
    """Update the cache entry for (user_id, block_id).

    Pass `md`, `html`, or both. Whichever is omitted keeps its prior
    value (so a markdown-only edit doesn't blow away a previously
    rendered HTML, and vice versa)."""
    if not block_id:
        return
    if html is None and md is None:
        return
    k = _key(user_id, block_id)
    entry = _cache.get(k) or CardEntry()
    if html is not None and isinstance(html, str):
        entry.html = html
    if md is not None and isinstance(md, str):
        entry.md = md
    _cache[k] = entry


def get_html(user_id: UUID, block_id: str) -> Optional[str]:
    entry = _cache.get(_key(user_id, block_id))
    return entry.html if entry is not None else None


def get_md(user_id: UUID, block_id: str) -> Optional[str]:
    entry = _cache.get(_key(user_id, block_id))
    return entry.md if entry is not None else None


def get(user_id: UUID, block_id: str) -> Optional[str]:
    """Back-compat: return the HTML string. New callers should use
    `get_html` / `get_md` explicitly."""
    return get_html(user_id, block_id)


def forget(user_id: UUID, block_id: str) -> None:
    _cache.pop(_key(user_id, block_id), None)


def forget_user(user_id: UUID) -> None:
    prefix = str(user_id)
    for k in [k for k in _cache if k[0] == prefix]:
        _cache.pop(k, None)


def clear() -> None:
    _cache.clear()
