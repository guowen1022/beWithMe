"""kickoff_log — chronological list of every Maestro long decision.

Emits one row per `maestro_long.kickoff_decision` event. PR-8's
trigger-gate training reads this view as the behavior-policy log;
propensity + candidate list travel in the event body for IPS.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.models.event import Event


async def materialize(session: AsyncSession, user_id: UUID) -> list[dict]:
    stmt = (
        select(Event)
        .where(
            Event.user_id == user_id,
            Event.kind == "maestro_long.kickoff_decision",
        )
        .order_by(asc(Event.ts))
    )
    rows = list((await session.execute(stmt)).scalars().all())
    out: list[dict] = []
    for row in rows:
        body = row.body or {}
        out.append({
            "event_id": str(row.event_id),
            "ts": row.ts.isoformat(),
            "decision": body.get("decision"),
            "rationale": body.get("rationale"),
            "propensity": body.get("propensity"),
            "triggering_event_id": body.get("triggering_event_id"),
            "triggering_kind": body.get("triggering_kind"),
            "k": body.get("k", 0),
            "candidates": body.get("candidates", []),
        })
    return out
