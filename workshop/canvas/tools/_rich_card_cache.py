"""Server-side cache of the current rendered HTML for each mounted rich_card.

Phase 2 of voice-leads: the canvas writer needs to see the *full HTML* of
an existing rich_card so it can decide between mounting fresh, appending
to it, highlighting parts of it, or revising specific text. The client
reports only a plaintext preview through `helpers.reportState({...})` —
not enough for surgical edits — so the server keeps its own cache,
updated whenever we know the rendered HTML changed:

  * `mount_template` (rich_card path) — after sanitize + diagram inlining
  * `push_block_content` (rich_card content topic) — after preprocessing
  * `edit_rich_card` — after applying ops and re-sanitizing

The cache is keyed by `(user_id, block_id)`. In-process only; mounts are
already ephemeral, so a workshop restart consistently drops both the
mounts and this cache.
"""
from __future__ import annotations

from typing import Optional, Tuple
from uuid import UUID


_cache: dict[Tuple[str, str], str] = {}


def _key(user_id: UUID, block_id: str) -> Tuple[str, str]:
    return (str(user_id), block_id)


def set(user_id: UUID, block_id: str, html: str) -> None:
    if not block_id or not isinstance(html, str):
        return
    _cache[_key(user_id, block_id)] = html


def get(user_id: UUID, block_id: str) -> Optional[str]:
    return _cache.get(_key(user_id, block_id))


def forget(user_id: UUID, block_id: str) -> None:
    _cache.pop(_key(user_id, block_id), None)


def forget_user(user_id: UUID) -> None:
    """Drop every cached HTML for this user. Used on canvas-wide unmount
    sweeps and tests."""
    prefix = str(user_id)
    for k in [k for k in _cache if k[0] == prefix]:
        _cache.pop(k, None)


def clear() -> None:
    """Test hook."""
    _cache.clear()
