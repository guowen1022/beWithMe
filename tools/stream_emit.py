"""stream_emit — append one observation to the user's event stream.

This is the agent's WRITE channel into the per-user event stream (SPEC §8).
Everything other than ACT tools that affect the world is supposed to flow
through here when it's worth remembering: an observation about the
learner, a noticed concept, a follow-up the agent wants the Maestro to
schedule. The Maestro and future agent turns read this stream back via
`query_stream` and `read_projection`.

Error contract matches the rest of the tool surface: any failure surfaces
as `{"error": "..."}` rather than raising, so the LLM can recover.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from infra.contracts.event import EventEmit
from infra.model.tools import ToolSpec
from infra.silicon_brain_client import SiliconBrainClient


# Source values the agent is allowed to stamp. The Maestro and signal
# emitters use other sources (`maestro_long`, `signal`, …) on different
# call paths.
_AGENT_SOURCES = {"agent", "user"}


async def stream_emit(
    *,
    user_id: UUID,
    kind: str,
    source: str = "agent",
    body: Optional[Dict[str, Any]] = None,
    refs: Optional[Dict[str, Any]] = None,
    valid_at: Optional[str] = None,
) -> Dict[str, Any]:
    kind = (kind or "").strip()
    if not kind:
        return {"error": "kind is required (e.g. 'agent.observation', 'agent.followup_scheduled')"}
    if source not in _AGENT_SOURCES:
        return {"error": f"source must be one of {sorted(_AGENT_SOURCES)} from the agent tool"}

    # `valid_at` arrives as an ISO-8601 string from the LLM; EventEmit
    # accepts either str or datetime via pydantic v2 coercion.
    emit = EventEmit(
        kind=kind,
        source=source,
        body=body or {},
        refs=refs,
        valid_at=valid_at,
    )

    client = SiliconBrainClient()
    try:
        try:
            row = await client.emit_event(user_id, emit)
        except Exception as e:
            return {"error": f"emit failed: {e}"}
        return {
            "event_id": str(row.event_id),
            "kind": row.kind,
            "ts": row.ts.isoformat(),
        }
    finally:
        await client.aclose()


def _make_stream_emit(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        kind = args.get("kind")
        if not isinstance(kind, str):
            return json.dumps({"error": "kind must be a string"})
        source = args.get("source") or "agent"
        body = args.get("body") or {}
        if not isinstance(body, dict):
            return json.dumps({"error": "body must be an object"})
        refs = args.get("refs")
        if refs is not None and not isinstance(refs, dict):
            return json.dumps({"error": "refs must be an object if provided"})
        valid_at = args.get("valid_at")
        result = await stream_emit(
            user_id=user_id,
            kind=kind,
            source=source,
            body=body,
            refs=refs,
            valid_at=valid_at,
        )
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="stream_emit",
        description=(
            "Append one event to this learner's durable event stream. "
            "Use this when you observe something about the learner that "
            "is surprising, non-obvious, or load-bearing for a later "
            "interaction — and would NOT be reconstructable from the "
            "current message log or the existing profile. The Maestro "
            "and your future turns read this stream back; over-emission "
            "drowns out the signal. Default to silence; emit when the "
            "next turn (yours or the Maestro's) would benefit.\n"
            "\n"
            "Kinds you author (`source` defaults to `agent`):\n"
            "  - `agent.observation` — something you noticed about this "
            "learner (a recurring confusion, a habit, a stated "
            "preference). `body`: {topic, observation, evidence}.\n"
            "  - `agent.followup_scheduled` — a thing the Maestro should "
            "consider raising later. `body`: {summary, why_now_isnt_right}, "
            "`valid_at`: ISO-8601 timestamp.\n"
            "  - `agent.concept_encountered` — a concept the learner "
            "touched in conversation. `body`: {concept_name, depth: "
            "'mention'|'discussed'|'applied'}.\n"
            "\n"
            "Discipline: emit what's *new and worth keeping*. If you're "
            "about to emit the same observation a second time, supersede "
            "the old event instead via `refs.supersedes`. If the "
            "observation contradicts an earlier one, supersede it. If "
            "you're tempted to emit a turn summary, don't — that's what "
            "the message log is for.\n"
            "\n"
            "Returns `{event_id, kind, ts}` on success or `{error}` on "
            "failure."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": (
                        "Open enum, dot-separated. Today's vocabulary "
                        "for the agent: `agent.observation`, "
                        "`agent.followup_scheduled`, "
                        "`agent.concept_encountered`. New kinds are "
                        "allowed when the body shape genuinely doesn't "
                        "fit — prefer reusing one of the three when "
                        "possible."
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": ["agent", "user"],
                    "description": (
                        "Defaults to `agent`. Use `user` ONLY when "
                        "you're recording an explicit user statement "
                        "verbatim (e.g. a declared aspiration)."
                    ),
                },
                "body": {
                    "type": "object",
                    "description": (
                        "JSON payload for the event. Shape depends on "
                        "`kind` — see kind list above. Keep it small "
                        "and structured; this gets read back on every "
                        "future Maestro query."
                    ),
                    "additionalProperties": True,
                },
                "refs": {
                    "type": "object",
                    "description": (
                        "Optional references to other events or domain "
                        "rows. Common keys: `supersedes` (event_id of "
                        "a prior event this replaces), `concept_id`, "
                        "`document_id`."
                    ),
                    "additionalProperties": True,
                },
                "valid_at": {
                    "type": "string",
                    "description": (
                        "ISO-8601 timestamp. Defaults to now. Use for "
                        "scheduled follow-ups: a future `valid_at` "
                        "makes the event appear in `due_followups` "
                        "only after that moment passes."
                    ),
                },
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        executor=_make_stream_emit(user_id),
    )


__all__ = ["stream_emit", "build_spec"]
