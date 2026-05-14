"""search_notes — teacher's tool for recalling its own authored notes.

The teacher has been mounting markdown notes throughout this and prior
sessions (see `mount_template(template='note', ...)` and `edit_note`).
This tool runs a semantic search over those notes' block-grouped chunks
so a current conversation can surface notes the teacher took earlier.

Returns up to `top_k` hits across all of the user's notes, ranked by
cosine distance. Each hit carries the note's `block_id` plus a
`(block_start, block_end)` span into the note's top-level markdown
blocks — the teacher can refer to the span verbatim or re-mount the
note via `mount_template` if it's no longer on canvas.

Errors come back as `{"error": "..."}` so the tool executor can hand
them straight back to the LLM.
"""
from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from persona.teacher.silicon_brain_client import SiliconBrainClient


async def search_notes(
    *,
    user_id: UUID,
    query: str,
    top_k: int = 5,
) -> Dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"error": "query is required"}
    client = SiliconBrainClient()
    try:
        try:
            hits = await client.search_notes(user_id, query, top_k=top_k)
        except Exception as e:
            return {"error": f"note search failed: {e}"}
        return {
            "query": query,
            "hits": [
                {
                    "note_id": h.note_id,
                    "block_start": h.block_start,
                    "block_end": h.block_end,
                    "text": h.text,
                    "score": h.score,
                }
                for h in hits
            ],
        }
    finally:
        await client.aclose()


__all__ = ["search_notes"]
