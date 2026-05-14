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


__all__ = ["enqueue_reembed", "enqueue_clear"]
