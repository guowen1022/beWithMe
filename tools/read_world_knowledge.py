"""read_world_knowledge — read the learner's world-knowledge cache.

Per IMPLEMENTATION.md §1.5, the world-knowledge cache is a per-learner
table populated lazily by the concept-extraction LLM call (SPEC §10.4
step 3). The table doesn't exist yet — it lands in a later PR. This
tool ships now so the agent's tool surface is contract-complete and
the LLM learns the calling pattern; until the table exists the tool
returns `{"items": [], "_stub": True, "reason": ...}`.

When the world-knowledge table lands, swap the stub body for a real
silicon_brain client call (e.g. `client.search_world_knowledge(...)`)
without touching the spec or the LLM-visible contract.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

from infra.model.tools import ToolSpec


async def read_world_knowledge(
    *,
    user_id: UUID,
    query: str,
    top_k: int = 5,
) -> Dict[str, Any]:
    return {
        "query": query,
        "items": [],
        "_stub": True,
        "reason": "world_knowledge_entry table is not yet implemented (planned per IMPLEMENTATION.md §1.5)",
    }


def _make_read_world_knowledge(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return json.dumps({"error": "query is required"})
        top_k_raw = args.get("top_k")
        try:
            top_k = int(top_k_raw) if top_k_raw is not None else 5
        except (TypeError, ValueError):
            return json.dumps({"error": "top_k must be an integer"})
        result = await read_world_knowledge(
            user_id=user_id, query=query.strip(), top_k=max(1, min(20, top_k)),
        )
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="read_world_knowledge",
        description=(
            "Look up cached world-knowledge entries about a topic for "
            "this learner. The cache is built up over time from the "
            "concept-extraction pass: when a concept the learner "
            "encounters has accumulated supporting context (definitions, "
            "key examples, common confusions), it lands here so future "
            "turns can ground without re-deriving.\n"
            "\n"
            "PHASE-0 STATUS: the underlying table is not yet "
            "implemented. The tool returns `{items: [], _stub: true, "
            "reason: '...'}` until a later PR fills it in. Calling it "
            "is still fine — treat `_stub: true` as 'no data', not "
            "'empty'. The contract (query + top_k → ranked items) "
            "will stay stable when the table lands.\n"
            "\n"
            "When to call (once implemented): when you'd otherwise "
            "spend tokens re-explaining a concept the learner has "
            "already met. A query like 'photosynthesis light "
            "reactions' returns recent contextual notes the system "
            "has cached for this learner specifically."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic phrase to look up.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Max items. Default 5.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        executor=_make_read_world_knowledge(user_id),
    )


__all__ = ["read_world_knowledge", "build_spec"]
