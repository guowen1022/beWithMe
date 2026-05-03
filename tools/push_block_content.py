"""push_block_content — teacher's tool for publishing into a block's bus topic.

Mirror of the public `POST /api/dynamic/push/{block_id}` endpoint but
invocable in-process. Frontend blocks subscribed to `topic` receive the
value through `bus.subscribe(topic, ...)`.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from infra.contracts.ui import BlockMessage
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user


async def push_block_content(
    *,
    user_id: UUID,
    block_id: str,
    topic: str,
    value: Any,
    target_device_id: Optional[UUID] = None,
) -> int:
    event = BlockMessage(block_id=block_id, topic=topic, value=value)
    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


__all__ = ["push_block_content"]
