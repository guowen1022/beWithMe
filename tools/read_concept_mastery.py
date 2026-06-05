"""read_concept_mastery — read this learner's concept graph + HLR mastery.

The teacher persona owns the concept graph (`persona/teacher/knowledge/`)
— concept nodes carry `half_life_hours` + `last_seen`, and mastery
probability is computed on read via HLR (`infra/hlr.py`). This tool
gives the agent loop a structured snapshot it can reason over.

Distinct from the prompt-side `summarise_mastery` (used to inject a
prose summary into the system prompt): this returns the per-concept
mastery + state numbers so the LLM can pick a specific concept to
review / extend / cite, and supports per-state filtering so a
review-oriented turn can ask "what's rusty?" without dragging in the
solid ones.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from infra.db import async_session
from infra.hlr import compute_mastery, mastery_to_state
from infra.model.tools import ToolSpec
from persona.teacher.knowledge import get_concepts


_VALID_STATES = ("solid", "learning", "rusty", "faded", "new")


def _serialise(node) -> Dict[str, Any]:
    now = datetime.utcnow()
    ref_time = node.last_recalled_at or node.last_seen
    if ref_time and ref_time.tzinfo is not None:
        ref_time = ref_time.replace(tzinfo=None)
    hours_since = max(0.0, (now - ref_time).total_seconds() / 3600.0) if ref_time else 0.0
    p = compute_mastery(node.half_life_hours, hours_since)
    return {
        "id": str(node.id),
        "name": node.name,
        "state": mastery_to_state(p),
        "mastery_p": round(p, 3),
        "half_life_hours": round(node.half_life_hours, 2),
        "encounter_count": node.encounter_count,
        "last_seen": node.last_seen.isoformat() if node.last_seen else None,
        "last_recalled_at": node.last_recalled_at.isoformat() if node.last_recalled_at else None,
    }


async def read_concept_mastery(
    *,
    user_id: UUID,
    state: Optional[str] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    if state is not None and state not in _VALID_STATES:
        return {"error": f"state must be one of {list(_VALID_STATES)}"}
    limit = max(1, min(100, int(limit)))

    async with async_session() as db:
        try:
            nodes = await get_concepts(db, user_id, state=state, limit=limit)
        except Exception as e:
            return {"error": f"read failed: {e}"}

    return {
        "count": len(nodes),
        "concepts": [_serialise(n) for n in nodes],
    }


def _make_read_concept_mastery(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        state = args.get("state")
        if state is not None and not isinstance(state, str):
            return json.dumps({"error": "state must be a string if provided"})
        limit_raw = args.get("limit")
        try:
            limit = int(limit_raw) if limit_raw is not None else 30
        except (TypeError, ValueError):
            return json.dumps({"error": "limit must be an integer"})
        result = await read_concept_mastery(user_id=user_id, state=state, limit=limit)
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="read_concept_mastery",
        description=(
            "Read this learner's concept-mastery snapshot — the concept "
            "nodes you've recorded for them, each with a computed "
            "mastery probability (0..1) and a coarse state (`solid`, "
            "`learning`, `rusty`, `faded`, `new`).\n"
            "\n"
            "The state of each concept decays over time via "
            "half-life regression — concepts you haven't surfaced "
            "recently slide from `solid` → `learning` → `rusty` → "
            "`faded`. Mastery climbs when you recall a concept in "
            "conversation (handled by other parts of the system); this "
            "tool only READS the current snapshot.\n"
            "\n"
            "When to call: when you're deciding what to surface next "
            "(target `rusty`/`faded` for a refresher; build on `solid` "
            "for advanced extensions); when the learner asks 'what do "
            "we know about X?' (filter by name in your response); when "
            "you're considering whether to schedule a `agent.followup_"
            "scheduled` event (a `faded` concept may be ripe).\n"
            "\n"
            "Returns `{count, concepts: [{id, name, state, mastery_p, "
            "half_life_hours, encounter_count, last_seen, "
            "last_recalled_at}]}`. Ordered by `last_seen` desc. Limit "
            "default 30, cap 100."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": list(_VALID_STATES),
                    "description": (
                        "Optional filter. e.g. 'rusty' to find concepts "
                        "ripe for review. Omit for all states."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Max concepts to return. Default 30.",
                },
            },
            "additionalProperties": False,
        },
        executor=_make_read_concept_mastery(user_id),
    )


__all__ = ["read_concept_mastery", "build_spec"]
