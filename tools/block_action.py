"""block_action — teacher's tool for invoking standard handles on a block.

Wraps the `BlockAction` SSE event. The frontend resolves `block_id` against
its dynamic-block registry and calls the matching handle (`highlight`,
`focus`, `scroll_to`).

If `target_device_id` is given, the event is delivered to that device only;
otherwise it fans out to every open SSE connection for the user.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from infra.contracts.ui import BlockAction
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user


async def block_action(
    *,
    user_id: UUID,
    block_id: str,
    action: str,
    options: Optional[Dict[str, Any]] = None,
    target_device_id: Optional[UUID] = None,
) -> int:
    """Send a BlockAction event. Returns the number of SSE queues it landed in.

    `action` must be one of: "highlight", "focus", "scroll_to". Anything
    else raises ValueError before touching the wire.
    """
    if action not in ("highlight", "focus", "scroll_to"):
        raise ValueError(f"unsupported block action: {action!r}")
    event = BlockAction(block_id=block_id, action=action, options=options or {})
    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


__all__ = ["block_action"]
