"""Unified learner state: assembles preferences, session signals, and concept mastery."""

import uuid
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.distill.models import ConceptNode, LearningPreferences
from app.distill.hlr import compute_mastery, mastery_to_state
from app.distill.preference_distiller import get_or_create_preferences
from app.models.interaction import Interaction


class ConceptSnapshot(BaseModel):
    name: str
    state: str
    mastery_p: float
    half_life_hours: float
    encounter_count: int
    last_seen: datetime

    model_config = {"from_attributes": True}


class LearnerState(BaseModel):
    # Durable preferences
    explanation_style: str = "balanced"
    depth_preference: str = "moderate"
    analogy_affinity: str = "moderate"
    math_comfort: str = "moderate"
    pacing: str = "moderate"
    meta_notes: str = ""
    # Session signals
    session_interest_summary: Optional[str] = None
    # Concept snapshot with live mastery
    concepts: List[ConceptSnapshot] = []


async def get_learner_state(
    db: AsyncSession,
    session_id: Optional[uuid.UUID] = None,
) -> LearnerState:
    """Assemble the full learner state: durable prefs + session focus + live concept mastery."""
    now = datetime.utcnow()

    def _make_naive(dt):
        """Strip timezone info for comparison with utcnow()."""
        if dt and dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt

    # 1. Durable preferences
    prefs = await get_or_create_preferences(db)

    # 2. Concept snapshot with live HLR mastery
    concept_result = await db.execute(
        select(ConceptNode)
        .order_by(ConceptNode.last_seen.desc())
        .limit(30)
    )
    concept_nodes = concept_result.scalars().all()

    concepts = []
    for node in concept_nodes:
        ref_time = _make_naive(node.last_recalled_at or node.last_seen)
        hours_since = max(0, (now - ref_time).total_seconds() / 3600.0)
        p = compute_mastery(node.half_life_hours, hours_since)
        concepts.append(ConceptSnapshot(
            name=node.name,
            state=mastery_to_state(p),
            mastery_p=round(p, 3),
            half_life_hours=node.half_life_hours,
            encounter_count=node.encounter_count,
            last_seen=node.last_seen,
        ))

    # 3. Session interest summary (last 3 questions in this session)
    session_summary = None
    if session_id:
        session_result = await db.execute(
            select(Interaction.question)
            .where(Interaction.session_id == session_id)
            .order_by(Interaction.created_at.desc())
            .limit(3)
        )
        recent_questions = [row[0] for row in session_result.all()]
        if recent_questions:
            session_summary = " | ".join(reversed(recent_questions))

    return LearnerState(
        explanation_style=prefs.explanation_style,
        depth_preference=prefs.depth_preference,
        analogy_affinity=prefs.analogy_affinity,
        math_comfort=prefs.math_comfort,
        pacing=prefs.pacing,
        meta_notes=prefs.meta_notes,
        session_interest_summary=session_summary,
        concepts=concepts,
    )
