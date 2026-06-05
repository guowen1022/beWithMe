"""due_followups — stub. Implemented in PR-3 / PR-4.

Will return `agent.followup_scheduled` events whose `valid_at` is in the
past and which have not yet been observed acted-on. The Maestro long
instance polls this projection (or subscribes to a `valid_at` index) to
fire scheduled-tick events into the event surface.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def materialize(session: AsyncSession, user_id: UUID) -> dict:
    return {"_stub": True, "name": "due_followups"}
