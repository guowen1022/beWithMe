"""recent_turns — stub. Implemented in PR-3.

Will return the last N `signal.turn_arrived` events with their `body`
content. The agent uses this for short-window context, distinct from the
durable interaction history that already lives in `interactions`.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def materialize(session: AsyncSession, user_id: UUID) -> dict:
    return {"_stub": True, "name": "recent_turns"}
