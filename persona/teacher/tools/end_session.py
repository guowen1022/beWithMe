"""end_session — wrap up the current learning session and return home.

A very thin tool: it prepares the parameters and calls the EXISTING
end-session API (`POST /api/sessions/{session_id}/end`, hosted by the
persona sidecar — saves the transcript, then summarizes in the
background), then emits the existing `go_home` app-action so the frontend
returns to the home feed. It reimplements no session logic of its own.

Why a tool at all: the teacher is an LLM and can only act through tools —
it can't call an HTTP endpoint from its reasoning loop. The end-session
API is reachable only by the frontend; this tool is the lever that lets
the teacher trigger it in response to what the user says.

Internal auth is just the `X-User-Id` header — sidecars trust same-network
calls (the shell already verified the user). Same pattern as the maestro
calls in `persona/teacher/engagement.py`.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

import httpx

from infra.contracts.ui import AppAction
from infra.devices.delivery import enqueue_for_user
from infra.model.tools import ToolSpec, ToolDomain
from infra.topology import upstream_url


def _make_end_session(user_id: UUID, session_id: Optional[UUID]):
    async def executor(args: Dict[str, Any]) -> str:
        saved = False
        # Voice-triggered turns carry no session_id — they still navigate
        # home, they just can't record (see persona/teacher/tools docs).
        if session_id is not None:
            url = f"{upstream_url('persona')}/api/sessions/{session_id}/end"
            try:
                async with httpx.AsyncClient(timeout=15.0, trust_env=False) as h:
                    resp = await h.post(url, headers={"X-User-Id": str(user_id)})
                # The endpoint 500s when the session has no interactions yet;
                # treat any non-200 as "nothing saved" and still go home.
                saved = resp.status_code == 200
            except Exception as e:  # never block navigation on a record failure
                print(f"[end_session] end-API call failed: {e}", flush=True)

        try:
            delivered = await enqueue_for_user(user_id, AppAction(action="go_home"))
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

        return json.dumps({"ok": True, "saved": saved, "delivered_to": delivered})

    return executor


def build_spec(user_id: UUID, session_id: Optional[UUID] = None) -> ToolSpec:
    return ToolSpec(
        name="end_session",
        description=(
            "End the current learning session and return the user to the home "
            "feed. Call this when the user signals they're finished OR wants to "
            "leave for the feed: 'end the session', 'I'm done', 'wrap up', "
            "'let's stop here', 'that's all for today', 'close this out', 'go "
            "home', 'take me home', 'go back to the feed', 'back to the home "
            "page'. This SAVES the session (transcript + summary) before "
            "leaving. Takes effect on the user's canvas immediately; you do not "
            "need to call speak afterward."
        ),
        params_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        executor=_make_end_session(user_id, session_id),
        domain=ToolDomain.TEACHER,
    )


__all__ = ["build_spec"]
