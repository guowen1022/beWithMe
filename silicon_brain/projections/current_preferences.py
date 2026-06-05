"""current_preferences — stub. Implemented in a later PR.

Will derive declared learning preferences (explanation_style, depth, …)
from `user.preference_stated` events. Domain table `user_preferences`
remains authoritative during Phase 0.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def materialize(session: AsyncSession, user_id: UUID) -> dict:
    return {"_stub": True, "name": "current_preferences"}
