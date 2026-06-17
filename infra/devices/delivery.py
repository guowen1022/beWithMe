"""Device delivery channel — the live SSE multiplexer + canvas mount tracker.

SSE only exists relative to a device/canvas, so the delivery channel is infra
device-domain machinery — not persona-router logic. This module owns:

  * `_subscribers`: one `asyncio.Queue` per open SSE connection, keyed
    `{user_id: {device_id: {Queue}}}`. Two tabs on the same device share a
    `device_id` and each gets its own queue under that slot.
  * `_mounted_blocks`: which block ids are currently on each device's canvas —
    flipped synchronously on every `UIUpdate` fan-out, BEFORE serialisation, so
    a follow-up `read_media` sees the new state even while SSE delivery is in
    flight. The perception cache holds per-block CONTENT; this holds the fact a
    block exists on the canvas at all.

The persona sidecar's `services/persona/routers/dynamic.py` is the HTTP face:
its `/dynamic/stream` endpoint calls `subscribe()`/`unsubscribe()` and loops the
returned queue; tools and canvas verbs call `enqueue_for_user`/`enqueue_for_device`.
This module imports only infra (`infra.contracts.ui`, `infra.perception`), so it
keeps infra's leaf position.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Set
from uuid import UUID

from pydantic import BaseModel

from infra import perception
from infra.contracts.ui import UIUpdate


# user_id (str) → device_id (str) → set of queues. One queue per open SSE
# connection. Two tabs on the same device = two queues under the same slot.
_subscribers: dict[str, dict[str, Set[asyncio.Queue]]] = defaultdict(lambda: defaultdict(set))


# user_id (str) → device_id (str) → set of block_ids currently mounted.
_mounted_blocks: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))


# TEMP diagnostic: mirror trace lines to a known file path so they can be
# grepped regardless of how dev-services was launched. Strip once the
# user-reported "teacher doesn't see PDF" bug is closed.
import time as _time
_TRACE_LOG_PATH = "/tmp/bewithme-perception-trace.log"


def _trace_log(line: str) -> None:
    print(line, flush=True)
    try:
        with open(_TRACE_LOG_PATH, "a") as f:
            f.write(f"{_time.strftime('%H:%M:%S')} {line}\n")
    except Exception:
        pass


def _record_mount_local(user_id_s: str, device_id_s: str, block_id: str) -> None:
    _mounted_blocks[user_id_s][device_id_s].add(block_id)
    _trace_log(
        f"[mount-tracker] MOUNT uid={user_id_s[:8]} did={device_id_s[:8]} bid={block_id} "
        f"=> set={sorted(_mounted_blocks[user_id_s][device_id_s])}"
    )


def _record_unmount_local(user_id_s: str, device_id_s: str, block_id: str) -> None:
    bucket = _mounted_blocks.get(user_id_s, {}).get(device_id_s)
    if not bucket:
        _trace_log(f"[mount-tracker] UNMOUNT uid={user_id_s[:8]} did={device_id_s[:8]} bid={block_id} (no bucket)")
        return
    bucket.discard(block_id)
    _trace_log(
        f"[mount-tracker] UNMOUNT uid={user_id_s[:8]} did={device_id_s[:8]} bid={block_id} "
        f"=> set={sorted(bucket)}"
    )
    if not bucket:
        _mounted_blocks[user_id_s].pop(device_id_s, None)
        if not _mounted_blocks.get(user_id_s):
            _mounted_blocks.pop(user_id_s, None)


def mounted_block_ids(user_id: UUID) -> dict[str, list[str]]:
    """Snapshot of currently-mounted block ids per device (string keys).

    Called by workshop.canvas.tools.read_media / list_media to compute
    which blocks are present on the user's canvases right now.
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


def subscribe(user_id: UUID, device_id: UUID) -> asyncio.Queue:
    """Open an SSE delivery queue for one user+device and return it.

    On a fresh client channel (no existing queue for this device) any cached
    state is stale — a full reload / Electron restart starts with zero blocks
    rendered, but our in-memory mount tracker and perception cache live for the
    process lifetime and would otherwise keep telling the teacher "block X is on
    canvas" long after the user can no longer see it. Reset both the moment the
    channel opens. Mid-session transient drops also flow through here — fine;
    the frontend's next state-report repopulates perception, and read_media
    unions cache + tracker.
    """
    uid_s, did_s = str(user_id), str(device_id)
    if did_s not in _subscribers.get(uid_s, {}):
        _mounted_blocks.get(uid_s, {}).pop(did_s, None)
        if uid_s in _mounted_blocks and not _mounted_blocks[uid_s]:
            _mounted_blocks.pop(uid_s, None)
        perception.forget_device(user_id=user_id, device_id=device_id)
        _trace_log(f"[mount-tracker] RESET-ON-OPEN uid={uid_s[:8]} did={did_s[:8]}")

    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[uid_s][did_s].add(queue)
    return queue


def unsubscribe(user_id: UUID, device_id: UUID, queue: asyncio.Queue) -> None:
    """Drop one SSE queue on disconnect.

    Deliberately does NOT sweep `_mounted_blocks`: any transient SSE drop
    (hot-reload, network blip, Electron tab-inactive timeout) would otherwise
    wipe the device's tracker and make the teacher say "canvas is empty" while
    the user stares at a fully-rendered PDF. Mount tracking lives for the
    process lifetime; ghosts get cleaned on the device's next channel open
    (RESET-ON-OPEN above) or an explicit unmount.
    """
    uid_s, did_s = str(user_id), str(device_id)
    queues = _subscribers.get(uid_s, {}).get(did_s)
    if queues is not None:
        queues.discard(queue)
        if not queues:
            _subscribers[uid_s].pop(did_s, None)
            if not _subscribers[uid_s]:
                _subscribers.pop(uid_s, None)
