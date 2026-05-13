"""push_block_content — teacher's tool for publishing into a block's bus topic.

Mirror of the public `POST /api/dynamic/push/{block_id}` endpoint but
invocable in-process. Frontend blocks subscribed to `topic` receive the
value through `bus.subscribe(topic, ...)`.

Per-template preprocessing: rich_card content updates run through
infra/render/rich_card.process so the persona's authored HTML is
sanitized + diagrams are pre-rendered the same way they were at
mount time. The block→template mapping is recorded by mount_template
(see workshop/canvas/tools/_template_registry).
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from infra.contracts.ui import BlockMessage
from infra.render.rich_card import process as preprocess_rich_card
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user
from workshop.canvas.tools import _template_registry


async def push_block_content(
    *,
    user_id: UUID,
    block_id: str,
    topic: str,
    value: Any,
    target_device_id: Optional[UUID] = None,
) -> int:
    # If this block was mounted from rich_card and the payload is a content
    # update string (matches the per-block content topic), run it through
    # the rich_card preprocessor so push-in-place updates get the same
    # sanitize + diagram-resolve as the initial mount.
    if (
        _template_registry.template_for(block_id) == "rich_card"
        and topic == f"text.{block_id}.content"
    ):
        if isinstance(value, str):
            value = await preprocess_rich_card(value)
        elif isinstance(value, dict) and isinstance(value.get("content"), str):
            value = {**value, "content": await preprocess_rich_card(value["content"])}

    event = BlockMessage(block_id=block_id, topic=topic, value=value)
    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


__all__ = ["push_block_content"]
