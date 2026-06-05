"""write_to_inbox — proactive proposal the user sees in the inbox surface.

Each call creates ONE inbox proposal (a card the user can tap or
dismiss). The Maestro long instance realises K candidates into K
proposals by calling this tool K times in parallel — they cluster in
the UI under `kickoff_event_id`. When the user taps a proposal, the
engagement helper seeds the cache with the proposal's `opening` +
`posture` so the first agent turn begins with that frame already
established (SPEC §6.1, §5.7).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from infra.contracts.inbox import InboxProposalCreate
from infra.model.tools import ToolSpec
from infra.silicon_brain_client import SiliconBrainClient


_VALID_POSTURES = frozenset({
    "steady", "deepen", "pivot", "hold",
    "wind_down", "escalate", "interrupt_now",
})


async def write_to_inbox(
    *,
    user_id: UUID,
    kickoff_event_id: UUID,
    candidate_idx: int,
    title: str,
    persona_purpose: str,
    posture: str,
    opening: str,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if posture not in _VALID_POSTURES:
        return {"error": f"posture must be one of {sorted(_VALID_POSTURES)}"}
    if not title.strip() or not opening.strip():
        return {"error": "title and opening are required"}

    proposal = InboxProposalCreate(
        kickoff_event_id=kickoff_event_id,
        candidate_idx=candidate_idx,
        title=title.strip(),
        persona_purpose=persona_purpose.strip(),
        posture=posture,
        opening=opening.strip(),
        body=body,
    )
    client = SiliconBrainClient()
    try:
        try:
            row = await client.write_inbox_proposal(user_id, proposal)
        except Exception as e:
            return {"error": f"write_to_inbox failed: {e}"}
        return {
            "id": str(row.id),
            "kickoff_event_id": str(row.kickoff_event_id),
            "candidate_idx": row.candidate_idx,
            "status": row.status,
            "posture": row.posture,
        }
    finally:
        await client.aclose()


def _make_write_to_inbox(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        try:
            kickoff_event_id = UUID(str(args.get("kickoff_event_id", "")))
        except (TypeError, ValueError):
            return json.dumps({"error": "kickoff_event_id must be a UUID string"})
        try:
            candidate_idx = int(args.get("candidate_idx", 0))
        except (TypeError, ValueError):
            return json.dumps({"error": "candidate_idx must be an integer"})

        title = args.get("title")
        opening = args.get("opening")
        persona_purpose = args.get("persona_purpose")
        posture = args.get("posture", "steady")
        body = args.get("body")
        if body is not None and not isinstance(body, dict):
            return json.dumps({"error": "body must be an object"})
        if not isinstance(title, str) or not isinstance(opening, str) or not isinstance(persona_purpose, str):
            return json.dumps({"error": "title, opening, persona_purpose required (strings)"})

        result = await write_to_inbox(
            user_id=user_id,
            kickoff_event_id=kickoff_event_id,
            candidate_idx=candidate_idx,
            title=title,
            persona_purpose=persona_purpose,
            posture=posture,
            opening=opening,
            body=body,
        )
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="write_to_inbox",
        description=(
            "Write ONE proactive proposal to the learner's inbox surface. "
            "Each call creates one card. To realize a K-candidate kickoff, "
            "call this K times in parallel — once per candidate. Cards "
            "from the same `kickoff_event_id` cluster in the UI under a "
            "shared header.\n"
            "\n"
            "When the user taps a card, the engagement helper seeds the "
            "Maestro cache with that candidate's `opening` + `posture`, "
            "so the first turn of the resulting engagement begins with "
            "the candidate's frame already established. Default to "
            "`posture: steady` unless the substrate clearly justifies "
            "another posture (e.g. `deepen` when mastery is high and "
            "the learner showed flow last engagement).\n"
            "\n"
            "Returns `{id, kickoff_event_id, candidate_idx, status, "
            "posture}` on success or `{error}` on failure."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "kickoff_event_id": {
                    "type": "string",
                    "description": (
                        "UUID of the `maestro_long.kickoff_decision` "
                        "event this proposal realises. The agent gets "
                        "this from the kickoff packet that woke it."
                    ),
                },
                "candidate_idx": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "0-based index of the candidate in the kickoff's list.",
                },
                "title": {
                    "type": "string",
                    "description": "Short label the user sees on the card (≤ 200 chars).",
                },
                "persona_purpose": {
                    "type": "string",
                    "description": (
                        "Tag like `teacher:long-horizon-propose`. Used "
                        "to key the Maestro cache when the proposal is "
                        "tapped."
                    ),
                },
                "posture": {
                    "type": "string",
                    "enum": sorted(_VALID_POSTURES),
                    "description": "SPEC §5.7 posture. Defaults to `steady`.",
                },
                "opening": {
                    "type": "string",
                    "description": (
                        "1-3 sentences that become the engagement's "
                        "first turn frame. The agent uses this as the "
                        "active cache paragraph."
                    ),
                },
                "body": {
                    "type": "object",
                    "description": "Optional extras (concept_names, source_link, etc.).",
                    "additionalProperties": True,
                },
            },
            "required": ["kickoff_event_id", "title", "persona_purpose", "opening"],
            "additionalProperties": False,
        },
        executor=_make_write_to_inbox(user_id),
    )


__all__ = ["write_to_inbox", "build_spec"]
