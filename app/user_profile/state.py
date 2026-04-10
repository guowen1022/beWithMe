"""User profile state: preferences, session signals, and preference embedding."""

import uuid
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.user_profile.ema import boost_query
from app.user_profile.preference_distiller import get_or_create_preferences
from app.models.interaction import Interaction


class UserProfileState(BaseModel):
    """Static user preferences — what the user_profile module owns."""
    # Durable preferences (categorical labels)
    explanation_style: str = "balanced"
    depth_preference: str = "moderate"
    analogy_affinity: str = "moderate"
    math_comfort: str = "moderate"
    pacing: str = "moderate"
    meta_notes: str = ""
    # Preference embedding (dense user fingerprint)
    preference_embedding: Optional[List[float]] = None
    # Session signals
    session_interest_summary: Optional[str] = None


async def get_user_profile(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: Optional[uuid.UUID] = None,
) -> UserProfileState:
    """Assemble the user profile: durable prefs + preference embedding + session focus."""
    # 1. Durable preferences
    prefs = await get_or_create_preferences(db, user_id)

    # 2. Session interest summary (last 3 questions in this session)
    session_summary = None
    if session_id:
        session_result = await db.execute(
            select(Interaction.question)
            .where(Interaction.user_id == user_id, Interaction.session_id == session_id)
            .order_by(Interaction.created_at.desc())
            .limit(3)
        )
        recent_questions = [row[0] for row in session_result.all()]
        if recent_questions:
            session_summary = " | ".join(reversed(recent_questions))

    return UserProfileState(
        explanation_style=prefs.explanation_style,
        depth_preference=prefs.depth_preference,
        analogy_affinity=prefs.analogy_affinity,
        math_comfort=prefs.math_comfort,
        pacing=prefs.pacing,
        meta_notes=prefs.meta_notes,
        preference_embedding=list(prefs.preference_embedding) if prefs.preference_embedding is not None else None,
        session_interest_summary=session_summary,
    )


async def boost_query_embedding(
    db: AsyncSession,
    user_id: uuid.UUID,
    query_embedding: List[float],
) -> List[float]:
    """Blend a query embedding with the user's preference embedding for retrieval.

    Returns the original query if no preference embedding exists yet.
    """
    prefs = await get_or_create_preferences(db, user_id)
    if prefs.preference_embedding:
        return boost_query(query_embedding, list(prefs.preference_embedding))
    return query_embedding
