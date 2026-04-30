"""Preference building: EMA-update preference embedding, auto-distill categorical labels."""

import uuid
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.silicon_brain.user_profile import (
    should_auto_distill,
    distill_preferences,
    get_or_create_preferences,
)
from app.silicon_brain.user_profile.ema import ema_update, zero_embedding


async def update_preference_embedding(
    db: AsyncSession,
    user_id: uuid.UUID,
    interaction_embedding: List[float],
) -> None:
    """Nudge the user's preference embedding toward a new interaction embedding."""
    try:
        prefs = await get_or_create_preferences(db, user_id)
        current = (
            [float(x) for x in prefs.preference_embedding]
            if prefs.preference_embedding is not None
            else zero_embedding()
        )
        updated = ema_update(current, [float(x) for x in interaction_embedding])
        prefs.preference_embedding = [float(x) for x in updated]
        await db.commit()
    except Exception as e:
        print(f"[brain_builder] Failed to update preference embedding: {e}", flush=True)


async def maybe_distill(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Auto-distill categorical preferences if enough interactions have accumulated."""
    try:
        if await should_auto_distill(db, user_id):
            print(f"[brain_builder] Auto-distilling", flush=True)
            await distill_preferences(db, user_id)
    except Exception as e:
        print(f"[brain_builder] Failed auto-distill: {e}", flush=True)
