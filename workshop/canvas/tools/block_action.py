"""block_action — teacher's tool for invoking standard handles on a block.

Wraps the `BlockAction` SSE event. The frontend resolves `block_id` against
its dynamic-block registry and calls the matching handle (`highlight`,
`focus`, `scroll_to`, `raise`).

If `target_device_id` is given, the event is delivered to that device only;
otherwise it fans out to every open SSE connection for the user.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from infra.contracts.ui import BlockAction
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user
from infra.model.tools import ToolSpec


_ALLOWED_ACTIONS = ("highlight", "focus", "scroll_to", "raise", "set_grid")


async def block_action(
    *,
    user_id: UUID,
    block_id: str,
    action: str,
    options: Optional[Dict[str, Any]] = None,
    target_device_id: Optional[UUID] = None,
) -> int:
    """Send a BlockAction event. Returns the number of SSE queues it landed in.

    `action` must be one of: "highlight", "focus", "scroll_to", "raise".
    Anything else raises ValueError before touching the wire.
    """
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported block action: {action!r}")
    event = BlockAction(block_id=block_id, action=action, options=options or {})
    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


__all__ = ["block_action", "build_spec"]

def _make_block_action(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        block_id = (args.get("block_id") or "").strip()
        action = (args.get("action") or "").strip()
        if not block_id or not action:
            return json.dumps({"error": "block_id and action are required"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        try:
            delivered = await block_action(
                user_id=user_id,
                block_id=block_id,
                action=action,
                options=args.get("options") or {},
                target_device_id=target_uuid,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"delivered_to": delivered})
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="block_action",
        description=(
            "Draw the user's attention to a surface already on the "
            "canvas. Actions: "
            "'highlight' (flash a glow), "
            "'focus' (move keyboard focus), "
            "'scroll_to' (scroll into view), "
            "'raise' (bring the surface to the front of the stack — "
            "use when one block is hidden behind another, e.g. you "
            "drew a diagram while a PDF was open and the user "
            "asks to see the PDF again, or you want to put a "
            "particular surface in front for emphasis). Newly-"
            "mounted surfaces are auto-raised, so you only need "
            "'raise' to flip the user back to a previously-mounted "
            "surface. Use the block_id you see in CURRENTLY ON "
            "CANVAS."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "block_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["highlight", "focus", "scroll_to", "raise"],
                },
                "options": {
                    "type": "object",
                    "description": "Action-specific options (e.g., highlight duration ms).",
                },
                "target_device_id": {"type": "string"},
            },
            "required": ["block_id", "action"],
            "additionalProperties": False,
        },
        executor=_make_block_action(user_id),
    )
