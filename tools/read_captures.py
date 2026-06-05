"""read_captures — read the learner's reality-capture inventory.

Per IMPLEMENTATION.md §1.6, reality captures are photos / voice memos /
screenshots / video the learner deliberately captured for the agent to
look at. They land in `silicon_brain/models/reality_capture.py` — a
table that doesn't exist yet. This tool ships now so the agent's tool
surface is contract-complete; until the table exists it returns an
empty list with `_stub: True`.

When the reality_capture table lands, swap the stub body for a real
silicon_brain client call (e.g. `client.list_captures(...)`) without
touching the spec or the LLM-visible contract.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from infra.model.tools import ToolSpec


async def read_captures(
    *,
    user_id: UUID,
    modality: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    return {
        "count": 0,
        "captures": [],
        "_stub": True,
        "reason": "reality_capture table is not yet implemented (planned per IMPLEMENTATION.md §1.6)",
    }


_VALID_MODALITIES = ("photo", "voice", "screenshot", "video")


def _make_read_captures(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        modality = args.get("modality")
        if modality is not None and modality not in _VALID_MODALITIES:
            return json.dumps({"error": f"modality must be one of {list(_VALID_MODALITIES)}"})
        since = args.get("since")
        limit_raw = args.get("limit")
        try:
            limit = int(limit_raw) if limit_raw is not None else 10
        except (TypeError, ValueError):
            return json.dumps({"error": "limit must be an integer"})
        result = await read_captures(
            user_id=user_id,
            modality=modality,
            since=since,
            limit=max(1, min(50, limit)),
        )
        return json.dumps(result)
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="read_captures",
        description=(
            "List recent reality-captures this learner has shared — "
            "photos, voice memos, screenshots, video they deliberately "
            "snapped for you to look at. Each capture carries a raw "
            "blob ref, extracted text, modality, and structural "
            "metadata. Pair with `look_at_image` / `look_at_video` to "
            "actually inspect the content; this tool just enumerates "
            "what's available.\n"
            "\n"
            "PHASE-0 STATUS: the underlying table is not yet "
            "implemented. The tool returns `{captures: [], _stub: true, "
            "reason: '...'}` until a later PR fills it in. The "
            "contract (modality + since + limit → ranked captures) "
            "will stay stable when the table lands.\n"
            "\n"
            "When to call (once implemented): when the learner says "
            "'remember that photo I took of the diagram' — without "
            "this call you'd ignore captures the learner expects you "
            "to recall. Filter by modality and time window to narrow."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "modality": {
                    "type": "string",
                    "enum": list(_VALID_MODALITIES),
                    "description": "Filter by capture type. Omit for all.",
                },
                "since": {
                    "type": "string",
                    "description": "ISO-8601 timestamp; captures with created_at >= since.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Max captures. Default 10.",
                },
            },
            "additionalProperties": False,
        },
        executor=_make_read_captures(user_id),
    )


__all__ = ["read_captures", "build_spec"]
