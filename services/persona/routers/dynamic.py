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
from infra import perception
from infra.perception.contracts import BlockState

from agents.frontend_engineer import llm_engineer

router = APIRouter()


# user_id (str) → device_id (str) → set of queues. One queue per open SSE
# connection. Two tabs on the same device = two queues under the same
# device slot.
_subscribers: dict[str, dict[str, Set[asyncio.Queue]]] = defaultdict(lambda: defaultdict(set))


# user_id (str) → device_id (str) → set of block_ids currently mounted.
# Authoritative lifecycle source — flipped synchronously on every UIUpdate
# fan-out, BEFORE serialisation. The perception cache (`infra.perception`)
# holds per-block CONTENT; this holds the fact that a block exists on
# the canvas at all. read_media reads BOTH and unions them, so the
# teacher knows a block is on screen the instant the server fans out
# the mount event — no waiting for the block to self-report.
_mounted_blocks: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))


def _record_mount_local(user_id_s: str, device_id_s: str, block_id: str) -> None:
    _mounted_blocks[user_id_s][device_id_s].add(block_id)


def _record_unmount_local(user_id_s: str, device_id_s: str, block_id: str) -> None:
    bucket = _mounted_blocks.get(user_id_s, {}).get(device_id_s)
    if not bucket:
        return
    bucket.discard(block_id)
    if not bucket:
        _mounted_blocks[user_id_s].pop(device_id_s, None)
        if not _mounted_blocks.get(user_id_s):
            _mounted_blocks.pop(user_id_s, None)


def mounted_block_ids(user_id: UUID) -> dict[str, list[str]]:
    """Snapshot of currently-mounted block ids per device (string keys).

    Called by tools.read_media / tools.list_media to compute which
    blocks are present on the user's canvases right now.
    """
    bucket = _mounted_blocks.get(str(user_id), {})
    return {did: sorted(blocks) for did, blocks in bucket.items() if blocks}


def _track_uiupdate(user_id_s: str, device_ids_s: list[str], event: BaseModel) -> None:
    """If the event is a UIUpdate, update the mount tracker for the listed
    devices. No-op for other event types.
    """
    if not isinstance(event, UIUpdate):
        return
    bid = event.block.id
    action = event.action
    for did_s in device_ids_s:
        if action == "mount":
            _record_mount_local(user_id_s, did_s, bid)
        elif action == "unmount":
            _record_unmount_local(user_id_s, did_s, bid)


def _serialize(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def enqueue_for_user(user_id: UUID, event: BaseModel) -> int:
    """Fan an event out to every queue for this user across all devices."""
    key = str(user_id)
    by_device = _subscribers.get(key, {})
    # Track mount/unmount BEFORE serialise/enqueue so a follow-up read
    # sees the new state even if the SSE delivery is still in flight.
    _track_uiupdate(key, list(by_device.keys()), event)
    payload = _serialize(event)
    delivered = 0
    for queues in list(by_device.values()):
        for q in list(queues):
            await q.put(payload)
            delivered += 1
    return delivered


async def enqueue_for_device(user_id: UUID, device_id: UUID, event: BaseModel) -> int:
    """Fan an event out to every queue for one device of this user."""
    _track_uiupdate(str(user_id), [str(device_id)], event)
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
            last_queue_for_device = False
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    last_queue_for_device = True
                    _subscribers[uid_s].pop(did_s, None)
                    if not _subscribers[uid_s]:
                        _subscribers.pop(uid_s, None)
            # When the device's last SSE connection closes, the browser
            # has nothing on screen anymore (page closed/reloaded; the
            # next page load starts with an empty canvas because
            # templates are ephemeral). Clear our mount tracker for
            # that device so the perception cache doesn't keep
            # hallucinating blocks from a previous session.
            if last_queue_for_device:
                stale_blocks = list(_mounted_blocks.get(uid_s, {}).get(did_s, set()))
                for stale_bid in stale_blocks:
                    _record_unmount_local(uid_s, did_s, stale_bid)
                # Also forget per-block content state so read_media stops
                # returning ghost entries for unmounted blocks.
                from infra.perception import forget_block as _forget_block
                for stale_bid in stale_blocks:
                    _forget_block(user_id=user_id, block_id=stale_bid)
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
    """Return every block currently persisted in the user's workspace.

    Used by the /canvas page to hydrate the dynamic surface on first
    load. Templates mounted via `mount_template` are ephemeral and
    won't appear here; only engineer-novel widgets (request_new_block)
    and arrow-overlay-style tool-owned blocks may persist.

    Before returning, run the one-shot mount-template migration: pre-
    this-architecture, every mount_template call wrote to git, so old
    workspaces have leftover template files. Sweep them here so the
    user gets a clean canvas on next reload.
    """
    # Local import: tools.mount_template imports from this module for
    # enqueue_for_user. Top-level would create a cycle.
    from tools.mount_template import _migrate_workspace_if_needed
    swept = await _migrate_workspace_if_needed(user_id)
    for stale_id in swept:
        await enqueue_for_user(user_id, UIUpdate(
            action="unmount",
            block=BlockSource(id=stale_id, source=""),
        ))
    return llm_engineer.list_blocks(user_id)


class _MountTemplateBody(BaseModel):
    template: str
    block_id: str | None = None
    grid: dict[str, int] | None = None
    replace: list[str] | None = None


@router.post("/dynamic/mount-template")
async def dynamic_mount_template(
    body: _MountTemplateBody,
    user_id: UUID = Depends(get_current_user_id),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
):
    """Materialize a built-in template into the user's workspace + canvas.

    The frontend uses this for the empty-canvas auto-mount and for the
    inputs_launcher's buttons. The persona could also call it as a tool
    (deferred to a follow-up).
    """
    # Local import: tools.mount_template imports from this router for
    # enqueue_for_user. Top-level would create a cycle.
    from tools.mount_template import mount_template

    try:
        device_uuid = UUID(x_device_id) if x_device_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Device-Id not a valid UUID")

    try:
        result = await mount_template(
            user_id=user_id,
            template_name=body.template,
            block_id=body.block_id,
            grid=body.grid,
            replace=body.replace,
            target_device_id=device_uuid,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown template: {body.template}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "block_id": result.block_id,
        "template": result.template,
        "deleted": result.deleted,
    }


@router.post("/dynamic/state/{block_id}")
async def dynamic_state(
    block_id: str,
    state: BlockState,
    user_id: UUID = Depends(get_current_user_id),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
):
    """Frontend pushes a block's current state to the perception cache.

    One-way: client → server. The cache is what the persona's read_media
    tool reads back. Returns 400 if no X-Device-Id header — every state
    report is per-device by design.
    """
    if not x_device_id:
        raise HTTPException(status_code=400, detail="X-Device-Id required")
    try:
        device_uuid = UUID(x_device_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Device-Id not a valid UUID")
    perception.record_block_state(
        user_id=user_id,
        device_id=device_uuid,
        block_id=block_id,
        state=state,
    )
    return {"recorded": True}
