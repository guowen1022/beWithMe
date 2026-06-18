"""app_operator agent — drives one turn through the shared tool loop.

App actions are quick and deterministic: the persona reads the request,
picks the single matching tool, it fires, and the turn ends. We cap the
loop tight (`max_iterations=2`) accordingly.

Reuses the generic loop in `infra/model/agent_loop.py` (relocated there from
the teacher so a second persona can share it without crossing the
persona-to-persona import boundary — ARCHITECTURE.md invariant #4).
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import UUID

from infra.model.agent_loop import run as run_tool_loop

from persona.app_operator.prompt import build_system
from persona.app_operator.tools.manifest import build_tools
from persona.app_operator.tools.grants import APP_OPERATOR_GRANT


async def respond(
    question: str,
    user_id: UUID,
    *,
    prior_messages: Optional[List[Dict[str, Any]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Run one app_operator turn; yield the loop's delta/tool_call/done events."""
    tools = build_tools(user_id)
    async for evt in run_tool_loop(
        static_system=build_system(),
        static_user_passage="",
        dynamic_user=question,
        prior_messages=prior_messages,
        tools=tools,
        max_iterations=2,
        purpose="app_operator",
        user_id=user_id,
        grant=APP_OPERATOR_GRANT,
    ):
        yield evt


__all__ = ["respond"]
