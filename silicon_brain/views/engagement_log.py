"""engagement_log — chronological list of this user's engagements.

Pairs `user.engagement_started` with the next `user.engagement_ended`
for the same `engagement_id`. An engagement currently in flight
(started, not yet ended) appears with `ended_at: null`. An engagement
that re-opened after a `user.engagement_ended` (re-engagement window
in `persona/teacher/engagement.py`) appears as two rows sharing the
same `engagement_id` — the run history is real and intentional.

Phase-0: Python materializer reads the user's stream and pairs in
memory. Volume is small (one row per engagement boundary). Phase 1+
can promote to a SQL view if/when read-side cost matters.

NOTE on "forbidden metrics" (SPEC §17): this view exposes
`started_at` + `ended_at` so consumers can compute duration if they
truly need to. The view itself never returns a `duration_*` field —
duration as a top-level metric is exactly the kind of "session length"
trap the SPEC forbids using as a reward. Consumers compute it on the
client side at their own risk.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional
from uuid import UUID

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.models.event import Event


_KINDS = ("user.engagement_started", "user.engagement_ended")


def _row(engagement_id: str, started_at, ended_at) -> dict:
    return {
        "engagement_id": engagement_id,
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
    }


async def materialize(session: AsyncSession, user_id: UUID) -> list[dict]:
    stmt = (
        select(Event)
        .where(Event.user_id == user_id, Event.kind.in_(_KINDS))
        .order_by(asc(Event.ts))
    )
    rows: list[Event] = list((await session.execute(stmt)).scalars().all())

    # Pair started→ended in stream order. Multiple started events for
    # the same engagement_id (re-engagement window) produce multiple
    # rows in the log — that is the truthful history.
    out: list[dict] = []
    open_for: "OrderedDict[str, object]" = OrderedDict()  # engagement_id → started_at

    for row in rows:
        body = row.body or {}
        eid: Optional[str] = body.get("engagement_id")
        if not eid:
            continue
        if row.kind == "user.engagement_started":
            # If this engagement_id was somehow still open without an end,
            # flush the prior open with ended_at=None first so we never
            # silently drop a started event.
            if eid in open_for:
                out.append(_row(eid, open_for.pop(eid), None))
            open_for[eid] = row.ts
        else:  # user.engagement_ended
            started_at = open_for.pop(eid, None)
            out.append(_row(eid, started_at, row.ts))

    # Drain anything still open (engagement currently in flight).
    for eid, started_at in open_for.items():
        out.append(_row(eid, started_at, None))

    return out
