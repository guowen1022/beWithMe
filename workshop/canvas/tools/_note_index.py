"""Persona → knowledge bridge for note search indexing.

When a note's markdown changes (mount, push, edit), we ship its block-grouped
chunks to the knowledge sidecar so its embeddings stay current. Fire-and-forget:
search degrades on failure, the note itself keeps working.

Lives next to `_note_cache.py` to keep workshop → infra purity (no persona
imports). Auth follows the SiliconBrainClient convention (X-User-Id header).
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import httpx

from infra.topology import upstream_url
from workshop.canvas.tools._note_chunker import chunk_note_markdown


_log = logging.getLogger(__name__)


async def _post(user_id: UUID, block_id: str, md: str) -> None:
    chunks = chunk_note_markdown(md) if md else []
    payload = {
        "note_id": block_id,
        "chunks": [
            {"block_start": s, "block_end": e, "text": t}
            for (s, e, t) in chunks
        ],
    }
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        try:
            resp = await client.post(
                f"{upstream_url('knowledge')}/api/notes/chunks",
                headers={"X-User-Id": str(user_id)},
                json=payload,
            )
            resp.raise_for_status()
        except Exception as e:
            _log.info("note re-index for %s failed: %s", block_id, e)


def _schedule(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    loop.create_task(coro)


def enqueue_reembed(user_id: UUID, block_id: str, md: str) -> None:
    """Schedule a fire-and-forget re-index of this note. No-op when `md`
    is empty (use `enqueue_clear` for explicit deletion)."""
    if not md or not md.strip():
        return
    _schedule(_post(user_id, block_id, md))


def enqueue_clear(user_id: UUID, block_id: str) -> None:
    """Drop all note_chunks for this (user_id, block_id) on the knowledge
    side. Called when a note is forgotten/unmounted."""
    _schedule(_post(user_id, block_id, ""))


async def search_similar(
    user_id: UUID,
    query: str,
    top_k: int = 5,
) -> dict[str, float]:
    """Semantic search against the user's stored notes via the knowledge
    sidecar. Returns slug → max-similarity-score (cosine ≈ 1 - distance)
    across all chunks of each note. Returns {} on any failure — callers
    treat semantic checks as best-effort.

    Used by `mount_template`'s slug-collision gate: token-set nesting
    alone false-positives on polysemy (`jobs` vs `steve-jobs`), so we
    AND it with content similarity before logging a confirmed collision.
    """
    if not query or not query.strip():
        return {}
    out: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(
                f"{upstream_url('knowledge')}/api/notes/search",
                headers={"X-User-Id": str(user_id)},
                params={"q": query[:2000], "top_k": top_k},
            )
            resp.raise_for_status()
            hits = resp.json() or []
            for h in hits:
                slug = h.get("note_id") if isinstance(h, dict) else None
                if not isinstance(slug, str) or not slug:
                    continue
                score = float(h.get("score", 0.0) or 0.0)
                if slug not in out or score > out[slug]:
                    out[slug] = score
    except Exception as e:
        _log.info("note search failed: %s", e)
    return out


__all__ = ["enqueue_reembed", "enqueue_clear", "search_similar"]
