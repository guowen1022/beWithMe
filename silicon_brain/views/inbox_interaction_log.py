"""inbox_interaction_log — every interaction the user took on a proposal.

Pairs naturally with kickoff_log: each kickoff_event_id should show up
in kickoff_log once (the Maestro's decision) and 0..K times in this
view (the user's tap/dismiss + the system's expiry). PR-8's gate
training reads this as the reward signal: tap = positive,
dismiss/expired = negative, "all K of a kickoff expired" = strong
silence-preferred on the original ACT decision.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.models.event import Event


_KINDS = (
    "user.proposal_tapped",
    "user.proposal_dismissed",
    "user.proposal_consumed",
    "system.proposal_expired",
)


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
            "kind": row.kind,
            "proposal_id": body.get("proposal_id"),
            "kickoff_event_id": body.get("kickoff_event_id"),
            "candidate_idx": body.get("candidate_idx"),
            "persona_purpose": body.get("persona_purpose"),
            "posture": body.get("posture"),
            "reason": body.get("reason"),   # only set on expiry
        })
    return out
