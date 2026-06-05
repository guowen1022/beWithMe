"""stream_projection — read a Phase-0 projection over the event stream.

Phase-0 projections are named, aggregated views over the per-user event
stream (SPEC §8.4). They're the cheap, structured way to ask "what's
the user's current state?" without scanning raw rows.

This PR (PR-2) ships the agent-side tool. The projections themselves
landed in PR-1 — six of them are stubs (return `{"_stub": True, ...}`)
until later PRs fill them in. The tool surfaces the stub markers so the
LLM can tell "I asked but there's nothing real here yet" apart from "I
asked and the projection said nothing matches."
"""
from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

from infra.model.tools import ToolSpec
from infra.silicon_brain_client import SiliconBrainClient


# Names listed for the LLM's enum. Mirrors silicon_brain.projections.PROJECTIONS
# so the schema is self-documenting; the server still 404s on unknown names.
_KNOWN_PROJECTIONS = (
    "current_engagement_state",
    "current_profile",
    "current_preferences",
    "due_followups",
    "current_aspirations",
    "recent_observations_by_topic",
    "recent_turns",
)


async def stream_projection(
    *,
    user_id: UUID,
    name: str,
) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"error": "name is required"}

    client = SiliconBrainClient()
    try:
        try:
            body = await client.read_projection(user_id, name)
        except Exception as e:
            return {"error": f"read failed: {e}"}
        return {"name": name, "projection": body}
    finally:
        await client.aclose()


def _make_stream_projection(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        name = args.get("name")
        if not isinstance(name, str):
            return json.dumps({"error": "name must be a string"})
        result = await stream_projection(user_id=user_id, name=name)
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="stream_projection",
        description=(
            "Read a named projection over this learner's event stream. "
            "Projections are pre-baked aggregations — much cheaper to "
            "reason about than raw events from `stream_query`.\n"
            "\n"
            "Available projections:\n"
            "  - `current_engagement_state` — `{status: 'active'|'idle', "
            "engagement_id?, started_at?, last_engagement?}`. "
            "Implemented.\n"
            "  - `due_followups` — `agent.followup_scheduled` events "
            "whose `valid_at` has passed. (Phase-0 stub today.)\n"
            "  - `current_profile` / `current_preferences` / "
            "`current_aspirations` — current snapshots derived from "
            "`user.*` events. (Phase-0 stubs today.)\n"
            "  - `recent_observations_by_topic` — your recent "
            "`agent.observation` events bucketed by topic. (Phase-0 "
            "stub today.)\n"
            "  - `recent_turns` — last few `signal.turn_arrived` "
            "events. (Phase-0 stub today.)\n"
            "\n"
            "If the returned `projection` body contains `_stub: true`, "
            "the projection isn't materialised yet — treat the answer "
            "as 'no data', not as 'empty'. Returns `{name, projection}` "
            "on success or `{error}` on failure (e.g. unknown name)."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": list(_KNOWN_PROJECTIONS),
                    "description": "Which projection to read.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        executor=_make_stream_projection(user_id),
    )


__all__ = ["stream_projection", "build_spec"]
