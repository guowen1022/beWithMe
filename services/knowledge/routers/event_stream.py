"""HTTP face of the per-user event stream (SPEC §8).

Distinct from `services/knowledge/routers/events.py`, which ingests
client-side observability into the JSONL log under `logs/`. This router
backs the durable per-user event stream the Maestro reasons over.

Routes (mounted under `/api` in `services/knowledge/main.py`):

    POST /api/event-stream                          — emit one event
    POST /api/event-stream/query                    — list events with filters
    GET  /api/event-stream/projections/{name}       — read a Phase-0 projection

`user_id` is taken from the `X-User-Id` header on every call (existing
sidecar convention). `ts` is server-stamped at insert time.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts.event import EventDTO, EventEmit, StreamQuery
from infra.db import get_db
from silicon_brain.models.event import Event
from silicon_brain.projections import PROJECTIONS


router = APIRouter()


def _to_dto(row: Event) -> EventDTO:
    return EventDTO(
        event_id=row.event_id,
        user_id=row.user_id,
        ts=row.ts,
        valid_at=row.valid_at,
        source=row.source,
        kind=row.kind,
        body=dict(row.body or {}),
        refs=dict(row.refs) if row.refs is not None else None,
        schema_version=row.schema_version,
    )


@router.post("/event-stream", response_model=EventDTO)
async def emit_event(
    body: EventEmit,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = Event(
        user_id=user_id,
        valid_at=body.valid_at,
        source=body.source,
        kind=body.kind,
        body=body.body,
        refs=body.refs,
        schema_version=body.schema_version,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_dto(row)


@router.post("/event-stream/query", response_model=list[EventDTO])
async def query_event_stream(
    body: StreamQuery,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    stmt = select(Event).where(Event.user_id == user_id)
    if body.kinds:
        stmt = stmt.where(Event.kind.in_(body.kinds))
    if body.sources:
        stmt = stmt.where(Event.source.in_(body.sources))
    if body.since is not None:
        stmt = stmt.where(Event.ts >= body.since)
    if body.until is not None:
        stmt = stmt.where(Event.ts < body.until)
    stmt = stmt.order_by(asc(Event.ts) if body.order == "asc" else desc(Event.ts))
    stmt = stmt.limit(body.limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dto(r) for r in rows]


@router.get("/event-stream/projections/{name}")
async def read_projection(
    name: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    materialize = PROJECTIONS.get(name)
    if materialize is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown projection '{name}'; known: {sorted(PROJECTIONS)}",
        )
    return await materialize(db, user_id)
