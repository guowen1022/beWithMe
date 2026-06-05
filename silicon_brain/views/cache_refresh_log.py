"""cache_refresh_log — chronological list of Maestro short decisions.

Emits one row per `maestro_short.cache_refresh` OR
`maestro_short.skip_refresh` event. Audits the short instance —
"posture changed unexpectedly", "refreshing too aggressively" — and
provides training signal for the Phase-1+ refresh policy.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.models.event import Event


_KINDS = ("maestro_short.cache_refresh", "maestro_short.skip_refresh")


async def materialize(session: AsyncSession, user_id: UUID) -> list[dict]:
    stmt = (
        select(Event)
        .where(Event.user_id == user_id, Event.kind.in_(_KINDS))
        .order_by(asc(Event.ts))
    )
    rows = list((await session.execute(stmt)).scalars().all())
    out: list[dict] = []
    for row in rows:
        body = row.body or {}
        out.append({
            "event_id": str(row.event_id),
            "ts": row.ts.isoformat(),
            "kind": row.kind,                  # cache_refresh or skip_refresh
            "decision": body.get("decision"),
            "rationale": body.get("rationale"),
            "signal_kind": body.get("signal_kind"),
            "triggering_event_id": body.get("triggering_event_id"),
            "prior_posture": body.get("prior_posture"),
            "new_posture": body.get("new_posture"),
            "posture_transition": body.get("posture_transition"),
        })
    return out
