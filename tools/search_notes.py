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

import json
from typing import Any, Dict
from uuid import UUID

from infra.silicon_brain_client import SiliconBrainClient
from infra.model.tools import ToolSpec


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


__all__ = ["search_notes", "build_spec"]

def _make_search_notes(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return json.dumps({"error": "query is required"})
        top_k_raw = args.get("top_k")
        try:
            top_k = int(top_k_raw) if top_k_raw is not None else 5
        except (TypeError, ValueError):
            return json.dumps({"error": "top_k must be an integer"})
        result = await search_notes(
            user_id=user_id,
            query=query,
            top_k=max(1, min(20, top_k)),
        )
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="search_notes",
        description=(
            "Recall passages from notes YOU previously authored with "
            "this user. When the user mentions a topic you've taught "
            "before — even loosely — call this to surface the relevant "
            "note(s) so you can build on what was already covered "
            "instead of starting from scratch. Vector search runs "
            "across all of this user's notes, not just the current "
            "session.\n"
            "\n"
            "Returns up to `top_k` hits, each tagged with: "
            "`note_id` (the block id the note was originally mounted "
            "under — pass to `mount_template(replace=[...])` or "
            "`edit_note(block_id=...)` if you want to re-surface or "
            "extend it), `block_start`/`block_end` (0-based indices "
            "into the note's top-level markdown blocks — useful to "
            "cite a specific span), `text` (the chunk's markdown, "
            "prefixed with the nearest preceding heading for "
            "context), and `score` (cosine similarity).\n"
            "\n"
            "Worked examples: user asks 'remind me how attention "
            "works?' → `search_notes(query='self-attention "
            "transformer')`. User says 'going back to that thing "
            "about gradients' → `search_notes(query='gradient "
            "descent backprop')`. Use a topic phrase, not a verbatim "
            "user sentence — terser queries embed better."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic phrase to match against your notes.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Max hits to return. Default 5.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        executor=_make_search_notes(user_id),
    )
