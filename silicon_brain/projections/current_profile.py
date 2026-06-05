"""current_profile — stub. Implemented in a later PR.

Will derive the user's current profile snapshot from `user.*` events
(self_description updates, declared preferences, etc.). For Phase 0 the
authoritative source remains `silicon_brain/models/profile.py`; this
projection exists so persona code can adopt the unified surface early
without waiting for the migration.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def materialize(session: AsyncSession, user_id: UUID) -> dict:
    return {"_stub": True, "name": "current_profile"}
