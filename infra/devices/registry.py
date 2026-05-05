"""Device registry — process-local live state + DB-mirrored durable state.

`_online[user_id][device_id] = ref_count` tracks how many SSE connections
we have open for that device. A user with two browser tabs on the same
laptop = ref_count 2 against the same device_id.

`register()` is called on SSE connect (full async, OK to await — happens
during the request hello). Disconnect cleanup is split:
  * `mark_offline_local()` updates the in-memory ref count synchronously.
  * `schedule_offline_write()` spawns a fire-and-forget last_seen UPDATE
    that survives the request task's cancellation when the SSE stream
    tears down. (Awaiting DB inside the cancelled request task leaves
    asyncpg connections half-closed in the pool.)

`list_for_user()` merges the two views: every DB row for the user, with
`online` flipped on for whoever the live registry currently knows about.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Dict, Set
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from infra.contracts.devices import Device, DeviceCapabilities, DeviceClass
from infra.db import async_session
from silicon_brain.models.device import Device as DeviceORM


# user_id (str) -> { device_id (str) -> ref_count }
_online: Dict[str, Dict[str, int]] = defaultdict(dict)

# Strong refs to spawned offline-write tasks so the GC doesn't drop them.
_pending_offline_writes: Set[asyncio.Task] = set()


def is_online(user_id: UUID, device_id: UUID) -> bool:
    return _online.get(str(user_id), {}).get(str(device_id), 0) > 0


async def register(
    *,
    user_id: UUID,
    device_id: UUID,
    device_class: DeviceClass = "desktop",
    capabilities: DeviceCapabilities | None = None,
) -> None:
    """Bump the ref count and UPSERT the DB row.

    Idempotent. The capabilities + device_class on the DB row are overwritten
    with whatever the latest connect declared — devices can change (granted
    mic permission since last visit, etc.).
    """
    caps = capabilities or DeviceCapabilities()
    now = datetime.utcnow()
    uid_s, did_s = str(user_id), str(device_id)
    _online[uid_s][did_s] = _online[uid_s].get(did_s, 0) + 1

    async with async_session() as session:
        stmt = pg_insert(DeviceORM).values(
            device_id=device_id,
            user_id=user_id,
            device_class=device_class,
            capabilities=caps.model_dump(),
            first_seen=now,
            last_seen=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DeviceORM.device_id],
            set_={
                # `user_id` MUST be in the update set. A device belongs
                # to whoever is currently signed in on it. If we keep
                # the row's user_id sticky to whoever first registered,
                # then any user who later signs in on the same device
                # is invisible to read_media — list_for_user filters
                # by DeviceORM.user_id, the device row stays under the
                # original user, the "current" user appears to have
                # no devices online. (Real-world hit: an Electron
                # session that previously held a bench profile, then
                # signed in as `default` — _online tracks the new user
                # but the DB row stayed under the old one, so the
                # teacher saw "NO ONLINE CANVASES" while a PDF was on
                # screen.)
                "user_id": user_id,
                "device_class": device_class,
                "capabilities": caps.model_dump(),
                "last_seen": now,
            },
        )
        await session.execute(stmt)
        await session.commit()


def mark_offline_local(*, user_id: UUID, device_id: UUID) -> None:
    """Synchronous in-memory ref-count decrement. Safe to call from a
    cancelled request task — does no I/O."""
    uid_s, did_s = str(user_id), str(device_id)
    bucket = _online.get(uid_s)
    if bucket and did_s in bucket:
        bucket[did_s] -= 1
        if bucket[did_s] <= 0:
            bucket.pop(did_s, None)
        if not bucket:
            _online.pop(uid_s, None)


async def _write_last_seen(device_id: UUID) -> None:
    try:
        async with async_session() as session:
            await session.execute(
                DeviceORM.__table__.update()
                .where(DeviceORM.device_id == device_id)
                .values(last_seen=datetime.utcnow())
            )
            await session.commit()
    except Exception as e:
        print(f"[devices] last_seen write failed for {device_id}: {e}", flush=True)


def schedule_offline_write(*, device_id: UUID) -> None:
    """Spawn the durable last_seen UPDATE as an independent task.

    Detached from the request task so SSE-disconnect cancellation doesn't
    kill the DB write half-way through and poison the connection pool.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_write_last_seen(device_id))
    _pending_offline_writes.add(task)
    task.add_done_callback(_pending_offline_writes.discard)


async def mark_offline(*, user_id: UUID, device_id: UUID) -> None:
    """Convenience wrapper for non-cancellation contexts (tests, scripts)."""
    mark_offline_local(user_id=user_id, device_id=device_id)
    await _write_last_seen(device_id)


async def list_for_user(user_id: UUID) -> list[Device]:
    """Every device this user has ever connected with, online flag attached."""
    async with async_session() as session:
        result = await session.execute(
            select(DeviceORM).where(DeviceORM.user_id == user_id)
        )
        rows = list(result.scalars().all())

    out: list[Device] = []
    for row in rows:
        out.append(
            Device(
                device_id=row.device_id,
                user_id=row.user_id,
                device_class=row.device_class,
                capabilities=DeviceCapabilities.model_validate(row.capabilities or {}),
                online=is_online(user_id, row.device_id),
                first_seen=row.first_seen,
                last_seen=row.last_seen,
            )
        )
    return out
