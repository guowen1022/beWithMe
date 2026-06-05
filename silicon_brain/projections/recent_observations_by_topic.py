"""recent_observations_by_topic — stub. Implemented in PR-2.

Will return `agent.observation` events from the last N days, bucketed by
their `topic` (or concept_id) ref. The agent reads this between turns to
recall what it has already noticed.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def materialize(session: AsyncSession, user_id: UUID) -> dict:
    return {"_stub": True, "name": "recent_observations_by_topic"}
