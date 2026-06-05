"""current_aspirations — stub. Implemented in a later PR.

Will derive the user's currently-declared aspirations (goals, intent
statements) from `user.aspiration_stated` and related events. Used by
the Maestro long instance during candidate generation.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def materialize(session: AsyncSession, user_id: UUID) -> dict:
    return {"_stub": True, "name": "current_aspirations"}
