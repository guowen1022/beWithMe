"""request_handoff — the lead pass's one routing tool.

The lead pass (the fast first LLM that hears the user) gives an immediate,
accurate response and decides — with guidance (`teacher/lead_routing`), not a
fixed rule — whether the turn needs more than the fast line can do. It carries
this single terminal tool and calls it to hand the turn off:

  * `target="deep"` — the turn needs looking at or acting on something the lead
    line can't reach on its own: inspect a diagram/image it drew, read the
    document, search the web, re-check produced work. The lead pass first
    streams a short honest acknowledgment ("let me take a closer look at that
    diagram"), THEN this call spawns the deep answering pass (full tool palette
    + access to the teacher's produced materials), which does the real work and
    replies.

  * `target="session"` — the user wants to act on the session itself (end it,
    stop, wrap up), not learn. Hands off to session control (Stage 2), which
    performs the action. The lead pass does NOT also reply when routing here.

When the lead pass can fully answer a simple question itself, it just answers
and never calls this tool (answer-now).

Terminal signal — it carries no action of its own. The ask router reads the
call (and its `target`) and dispatches to the right Stage-2 pass. Replaces the
former single-purpose `request_session_control`.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

from infra.model.tools import ToolSpec, ToolDomain

NAME = "request_handoff"
TARGET_SESSION = "session"
TARGET_DEEP = "deep"


def _make_executor(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        # Terminal signal — the real handling happens in the ask router, which
        # reads `target` and routes to Stage 2. The return value is not used.
        target = (args or {}).get("target")
        return json.dumps({"ok": True, "routed": target})
    return executor


def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name=NAME,
        description=(
            "Hand this turn off to a deeper pass instead of finishing it on the "
            "fast line. Call with target='deep' when fulfilling the user needs "
            "looking at or acting on something you cannot reach right now — "
            "inspecting a diagram or image you drew, reading the document, "
            "searching the web, or re-checking work you produced. Before you "
            "call it, say a short honest acknowledgment out loud (e.g. 'Sure — "
            "let me take a closer look at that diagram'); the deep pass then "
            "does the real work and replies. Call with target='session' when "
            "the user wants to act on the SESSION itself — end it, stop, wrap "
            "up — not learn; in that case do NOT also reply. A question that "
            "merely mentions 'session' as a topic (the OSI session layer, HTTP "
            "sessions) is NOT this — answer it normally. If you can fully answer "
            "a simple question yourself right now, just answer — do not call "
            "this tool. NEVER tell the user you can't see or do something: if it "
            "needs a closer look, route deep instead of disclaiming."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": [TARGET_SESSION, TARGET_DEEP],
                    "description": (
                        "Where to hand off: 'deep' for a deeper look/action the "
                        "fast line can't do; 'session' for session control "
                        "(end/stop/wrap up)."
                    ),
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        executor=_make_executor(user_id),
        domain=ToolDomain.TEACHER,
    )


__all__ = ["build_spec", "NAME", "TARGET_SESSION", "TARGET_DEEP"]
