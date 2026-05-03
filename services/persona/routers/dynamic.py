"""Dynamic UI back-channel — SSE multiplexer + push/error endpoints.

Three endpoints:
  GET  /api/dynamic/stream             — SSE channel keyed by (user_id, device_id).
                                          Carries UIUpdate, BlockMessage,
                                          BlockError events for that user/device.
  POST /api/dynamic/push/{block_id}    — body {topic, value}; fans out a
                                          BlockMessage to the user's stream.
  POST /api/dynamic/error/{block_id}   — frontend reports browser-side eval
                                          failures here; fans out a BlockError.

The registry is in-memory (one asyncio.Queue per active SSE connection)
and shared with `tools/request_ui_block.py` via `enqueue_for_user`. Devices
are registered with `infra.devices.registry` on connect so `list_media()`
can report what's online.

`_subscribers` is now nested: `{user_id: {device_id: {Queue}}}`. Two browser
tabs from the same device share a `device_id` and each gets its own queue
under the same slot. `enqueue_for_user` fans out across all devices for
backward compatibility; `enqueue_for_device` targets one.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Set
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from infra.auth import parse_user_id as get_current_user_id
from infra.contracts.devices import DeviceCapabilities
from infra.contracts.ui import BlockError, BlockMessage, BlockSource, UIUpdate
from infra.devices import registry as device_registry

from agents.frontend_engineer import llm_engineer

router = APIRouter()


# user_id (str) → device_id (str) → set of queues. One queue per open SSE
# connection. Two tabs on the same device = two queues under the same
# device slot.
_subscribers: dict[str, dict[str, Set[asyncio.Queue]]] = defaultdict(lambda: defaultdict(set))


def _serialize(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def enqueue_for_user(user_id: UUID, event: BaseModel) -> int:
    """Fan an event out to every queue for this user across all devices."""
    key = str(user_id)
    payload = _serialize(event)
    delivered = 0
    by_device = _subscribers.get(key, {})
    for queues in list(by_device.values()):
        for q in list(queues):
            await q.put(payload)
            delivered += 1
    return delivered


async def enqueue_for_device(user_id: UUID, device_id: UUID, event: BaseModel) -> int:
    """Fan an event out to every queue for one device of this user."""
    payload = _serialize(event)
    delivered = 0
    queues = _subscribers.get(str(user_id), {}).get(str(device_id), set())
    for q in list(queues):
        await q.put(payload)
        delivered += 1
    return delivered


def _parse_capabilities(raw: str | None) -> DeviceCapabilities:
    if not raw:
        return DeviceCapabilities()
    try:
        return DeviceCapabilities.model_validate(json.loads(raw))
    except Exception:
        return DeviceCapabilities()


def _parse_device_class(raw: str | None) -> str:
    val = (raw or "").strip().lower()
    if val in {"phone", "tablet", "desktop"}:
        return val
    return "desktop"


@router.get("/dynamic/stream")
async def dynamic_stream(
    request: Request,
    user_id: UUID = Depends(get_current_user_id),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_class: str | None = Header(default=None, alias="X-Device-Class"),
    x_device_capabilities: str | None = Header(
        default=None, alias="X-Device-Capabilities"
    ),
):
    """SSE channel for one user+device. Stays open until the client disconnects."""
    # Tolerate clients that haven't adopted device headers yet — synthesize a
    # one-shot device_id so they still get a working stream. Multi-device
    # features won't apply to them, but they don't break.
    try:
        device_id = UUID(x_device_id) if x_device_id else uuid4()
    except ValueError:
        device_id = uuid4()
    device_class = _parse_device_class(x_device_class)
    capabilities = _parse_capabilities(x_device_capabilities)

    await device_registry.register(
        user_id=user_id,
        device_id=device_id,
        device_class=device_class,
        capabilities=capabilities,
    )

    queue: asyncio.Queue = asyncio.Queue()
    uid_s, did_s = str(user_id), str(device_id)
    _subscribers[uid_s][did_s].add(queue)

    async def gen():
        try:
            # Initial hello — proves the channel is open even before any
            # block ships. Carries the device_id back so the client can
            # confirm what the server registered (matters when the client
            # didn't send one and we minted a fresh UUID).
            yield f"data: {json.dumps({'type': 'open', 'device_id': did_s})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    # SSE keepalive comment — keeps proxies and idle TCP
                    # paths from closing the stream.
                    yield ": keepalive\n\n"
        finally:
            queues = _subscribers.get(uid_s, {}).get(did_s)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    _subscribers[uid_s].pop(did_s, None)
                    if not _subscribers[uid_s]:
                        _subscribers.pop(uid_s, None)
            # Don't await the DB write here — uvicorn cancels this task on
            # client disconnect, and an in-flight asyncpg call inside a
            # cancelled task leaves the pooled connection in a broken state.
            device_registry.mark_offline_local(user_id=user_id, device_id=device_id)
            device_registry.schedule_offline_write(device_id=device_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class _PushBody(BaseModel):
    topic: str
    value: Any = None


@router.post("/dynamic/push/{block_id}")
async def dynamic_push(
    block_id: str,
    body: _PushBody,
    user_id: UUID = Depends(get_current_user_id),
):
    """Send a value to a topic on a mounted block."""
    event = BlockMessage(block_id=block_id, topic=body.topic, value=body.value)
    delivered = await enqueue_for_user(user_id, event)
    if delivered == 0:
        # Soft signal — caller may have raced the SSE connection. The block
        # bus is sticky on the client so a slightly-late publish still wins,
        # but with no open stream we have no client to publish to.
        raise HTTPException(status_code=409, detail="no active dynamic stream")
    return {"delivered_to": delivered}


class _ErrorBody(BaseModel):
    error: str


@router.post("/dynamic/error/{block_id}")
async def dynamic_error(
    block_id: str,
    body: _ErrorBody,
    user_id: UUID = Depends(get_current_user_id),
):
    """Frontend reports a browser-side eval/run failure for a block."""
    event = BlockError(block_id=block_id, error=body.error)
    await enqueue_for_user(user_id, event)
    print(f"[dynamic] block_error user={user_id} block={block_id} err={body.error!r}", flush=True)
    return {"ok": True}


@router.get("/dynamic/canvas")
async def dynamic_canvas(
    user_id: UUID = Depends(get_current_user_id),
) -> list[BlockSource]:
    """Return every block currently in the user's per-user-git workspace.

    Used by the /canvas page to hydrate the dynamic surface on first load —
    so blocks survive page reloads. The list mirrors what mount events
    would deliver if the user re-issued every command they've ever run.
    """
    return llm_engineer.list_blocks(user_id)
