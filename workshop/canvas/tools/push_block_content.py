"""push_block_content — teacher's tool for publishing into a block's bus topic.

Mirror of the public `POST /api/dynamic/push/{block_id}` endpoint but
invocable in-process. Frontend blocks subscribed to `topic` receive the
value through `bus.subscribe(topic, ...)`.

Per-template preprocessing: note content updates run through
infra/render/note.process so the persona's authored HTML is
sanitized + diagrams are pre-rendered the same way they were at
mount time. The block→template mapping is recorded by mount_template
(see workshop/canvas/tools/_template_registry).
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from infra.contracts.ui import BlockMessage
from infra.render.note import process as preprocess_note
from infra.render.note_md import render_markdown as render_note_markdown
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from workshop.canvas.tools import _note_cache, _note_index, _template_registry
from infra.model.tools import ToolSpec


async def push_block_content(
    *,
    user_id: UUID,
    block_id: str,
    topic: str,
    value: Any,
    target_device_id: Optional[UUID] = None,
) -> int:
    # If this block was mounted from note and the payload is a content
    # update string (matches the per-block content topic), run it through
    # the note preprocessor so push-in-place updates get the same
    # sanitize + diagram-resolve as the initial mount.
    #
    # Two authoring surfaces, same as mount_template:
    #   * `{markdown: "..."}` — preferred. Renders via note_md and
    #     caches both md + html.
    #   * raw HTML string or `{content: "<html>"}` — legacy; sanitized
    #     and cached as html-only.
    if (
        _template_registry.template_for(block_id) == "note"
        and topic == f"text.{block_id}.content"
    ):
        if isinstance(value, dict) and isinstance(value.get("markdown"), str):
            md_source = value["markdown"]
            processed = await render_note_markdown(md_source)
            # Ship the rendered HTML to the client on the existing
            # content shape so the note.js subscribe handler keeps
            # working unchanged.
            value = {**{k: v for k, v in value.items() if k != "markdown"}, "content": processed}
            _note_cache.set(user_id, block_id, html=processed, md=md_source)
            _note_index.enqueue_reembed(user_id, block_id, md_source)
        elif isinstance(value, str):
            value = await preprocess_note(value)
            _note_cache.set(user_id, block_id, html=value)
        elif isinstance(value, dict) and isinstance(value.get("content"), str):
            processed = await preprocess_note(value["content"])
            value = {**value, "content": processed}
            _note_cache.set(user_id, block_id, html=processed)

    event = BlockMessage(block_id=block_id, topic=topic, value=value)
    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


__all__ = ["push_block_content", "build_spec"]

def _make_push_block_content(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        block_id = (args.get("block_id") or "").strip()
        topic = (args.get("topic") or "").strip()
        if not block_id or not topic:
            return json.dumps({"error": "block_id and topic are required"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        delivered = await push_block_content(
            user_id=user_id,
            block_id=block_id,
            topic=topic,
            value=args.get("value"),
            target_device_id=target_uuid,
        )
        return json.dumps({"delivered_to": delivered})
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="push_block_content",
        description=(
            "Send a value into a topic that an existing surface "
            "listens on. Use to drive live data (counters, list rows, "
            "text updates) into something already up — no remount."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "block_id": {"type": "string"},
                "topic": {"type": "string"},
                "value": {
                    "description": "Any JSON value the block expects on this topic.",
                },
                "target_device_id": {"type": "string"},
            },
            "required": ["block_id", "topic"],
            "additionalProperties": False,
        },
        executor=_make_push_block_content(user_id),
    )
