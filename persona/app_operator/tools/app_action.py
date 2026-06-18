"""app_action — the "app actions" tool set owned by the app_operator persona.

App-level verbs that change the UI *shell* rather than a single block:
  * switch_user — sign the current user out → the account picker
  * go_home     — leave the current session → the home launcher feed
  * show_mirror — mount the read-only event-stream Mirror on the canvas

`switch_user` / `go_home` wrap the `AppAction` SSE event (the app-scoped
sibling of `BlockAction`); the frontend's DynamicSurface handler re-dispatches
them onto the `bewithme:*` window events `App.tsx` owns. `show_mirror` reuses
the canvas `mount_template` core to mount the `mirror` template.

`build_app_action_specs(user_id)` returns the per-request `ToolSpec` list with
executors bound to `user_id`, mirroring `tools.build_generic_specs` and
`workshop.canvas.tools.build_canvas_specs`.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from infra.contracts.ui import AppAction
from infra.model.tools import ToolSpec, ToolDomain
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from workshop.canvas.tools.mount_template import mount_template


async def app_action(
    *,
    user_id: UUID,
    action: str,
    target: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    target_device_id: Optional[UUID] = None,
) -> int:
    """Emit one `AppAction` SSE event. Returns the number of SSE queues it
    landed in. `action` must be a member of `AppAction`'s Literal (validated
    by the pydantic model on construction)."""
    event = AppAction(action=action, target=target, options=options or {})
    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


# --- switch_user -----------------------------------------------------------

def _make_switch_user(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        raw = args.get("user_id")
        target = raw.strip() if isinstance(raw, str) and raw.strip() else None
        try:
            delivered = await app_action(
                user_id=user_id, action="switch_user", target=target,
            )
        except Exception as e:  # pragma: no cover - defensive
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return json.dumps(
            {"ok": True, "action": "switch_user", "delivered_to": delivered}
        )
    return executor


def _switch_user_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="switch_user",
        description=(
            "Sign the current user out and return to the account picker so a "
            "different person can sign in. Use when the user asks to switch "
            "accounts, log out, or use a different profile. Takes effect on "
            "the user's active canvas immediately."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": (
                        "Optional id of a user to switch to. Usually omit — the "
                        "account picker lets the user choose."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        executor=_make_switch_user(user_id),
        domain=ToolDomain.APP,
    )


# --- go_home ---------------------------------------------------------------

def _make_go_home(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        try:
            delivered = await app_action(user_id=user_id, action="go_home")
        except Exception as e:  # pragma: no cover - defensive
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return json.dumps(
            {"ok": True, "action": "go_home", "delivered_to": delivered}
        )
    return executor


def _go_home_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="go_home",
        description=(
            "Leave the current reader/canvas session and return to the home "
            "launcher feed. Use when the user asks to go home, go back to the "
            "feed, or start over."
        ),
        params_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        executor=_make_go_home(user_id),
        domain=ToolDomain.APP,
    )


# --- show_mirror -----------------------------------------------------------

def _make_show_mirror(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        try:
            result = await mount_template(user_id=user_id, template_name="mirror")
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return json.dumps(
            {"ok": True, "action": "show_mirror", "block_id": result.block_id}
        )
    return executor


def _show_mirror_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="show_mirror",
        description=(
            "Open the Mirror on the canvas: a read-only panel showing the "
            "user's event stream — every event the system recorded for them, "
            "grouped by source (you, agent, maestro, …). Use when the user "
            "asks to see their mirror, their activity, their history, or what "
            "the system knows about them."
        ),
        params_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        executor=_make_show_mirror(user_id),
        domain=ToolDomain.APP,
    )


def build_app_action_specs(user_id: UUID) -> List[ToolSpec]:
    """Return the app_operator persona's tool allowlist."""
    return [
        _switch_user_spec(user_id),
        _go_home_spec(user_id),
        _show_mirror_spec(user_id),
    ]


__all__ = ["app_action", "build_app_action_specs"]
