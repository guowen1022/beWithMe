"""request_session_control — Stage-1 hand-off signal.

The teacher's fast line (the first LLM that hears the user) decides, with
guidance, whether the turn is *inside* the teaching loop (a question, going
deeper) or *outside* it (the user wants to act on the session — end it, stop).
Only the model can tell those apart: "explain the OSI session layer" is a
question to teach; "okay I'm done, end it" is a request to leave. A fixed
string rule would confuse the two.

When the model judges the turn to be outside the teaching loop, it calls this
tool. It carries no action of its own — it's a terminal signal. The ask router
sees the call, suppresses the normal spoken reply + canvas draw, and opens the
session-control decision tree (Stage 2: build_session_tools), where the model
picks the actual session tool (today: end_session).
"""
from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

from infra.model.tools import ToolSpec, ToolDomain

NAME = "request_session_control"


def _make_executor(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        # Terminal signal — the real handling happens in the ask router, which
        # routes to Stage 2. The return value is not used by anything.
        return json.dumps({"ok": True, "routed": "session_control"})
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name=NAME,
        description=(
            "Hand off to session control. Call this ONLY when the user wants to "
            "step OUTSIDE the teaching conversation — end the session, stop, wrap "
            "up, leave — instead of asking a question, answering, or going deeper "
            "into the material. A question that merely mentions 'session' as a "
            "topic (e.g. the OSI session layer, HTTP sessions) is NOT this — teach "
            "it normally. When you call this, do NOT also reply or draw: session "
            "control takes over and performs the action."
        ),
        params_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        executor=_make_executor(user_id),
        domain=ToolDomain.TEACHER,
    )


__all__ = ["build_spec", "NAME"]
