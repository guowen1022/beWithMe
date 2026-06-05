"""current_engagement_state — is this user currently in an engagement?

Derived from `user.engagement_started` / `user.engagement_ended` events.
The most recent of the two determines status. Returns:

    {"status": "idle"}
        — no engagement events yet, or the latest is `engagement_ended`

    {"status": "active", "engagement_id": "...", "started_at": "..."}
        — the latest event is `engagement_started`

    {"status": "idle", "last_engagement": {...}}
        — there was a past engagement; carries its summary as context

Phase-0 reference implementation. The other six Phase-0 projections are
stubs until the PR that consumes them lands (PR-3 / PR-5 / PR-6 / PR-7).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.models.event import Event


_KINDS = ("user.engagement_started", "user.engagement_ended")


async def materialize(session: AsyncSession, user_id: UUID) -> dict:
    stmt = (
        select(Event)
        .where(Event.user_id == user_id, Event.kind.in_(_KINDS))
        .order_by(desc(Event.ts))
        .limit(1)
    )
    latest = (await session.execute(stmt)).scalar_one_or_none()

    if latest is None:
        return {"status": "idle"}

    body = latest.body or {}
    if latest.kind == "user.engagement_started":
        return {
            "status": "active",
            "engagement_id": body.get("engagement_id"),
            "started_at": latest.ts.isoformat(),
        }

    return {
        "status": "idle",
        "last_engagement": {
            "engagement_id": body.get("engagement_id"),
            "ended_at": latest.ts.isoformat(),
        },
    }
