"""LLM-based distillation of learning preferences from interaction history."""

import json
import re
import uuid
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.interaction import Interaction
from app.user_profile.models import LearningPreferences
from app.services.llm import generate

DISTILL_PROMPT = """Analyze these recent reading interactions and produce a learning preferences profile.

INTERACTIONS:
{interactions}

Based on how the user asks questions, what they focus on, what confuses them, and how they respond, produce a JSON object with these fields:

- "explanation_style": one of "formal", "conversational", "socratic", "narrative", "balanced"
- "depth_preference": one of "shallow", "moderate", "deep"
- "analogy_affinity": one of "low", "moderate", "high"
- "math_comfort": one of "avoid", "moderate", "fluent"
- "pacing": one of "concise", "moderate", "detailed"
- "meta_notes": 1-3 sentences describing this user's learning patterns, what topics they gravitate toward, and any notable behaviors

Return ONLY valid JSON, no other text."""


async def get_or_create_preferences(db: AsyncSession, user_id: uuid.UUID) -> LearningPreferences:
    result = await db.execute(
        select(LearningPreferences).where(LearningPreferences.user_id == user_id)
    )
    prefs = result.scalar_one_or_none()
    if not prefs:
        prefs = LearningPreferences(user_id=user_id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return prefs


async def distill_preferences(db: AsyncSession, user_id: uuid.UUID) -> LearningPreferences:
    """Distill learning preferences from recent interactions."""
    result = await db.execute(
        select(Interaction)
        .where(Interaction.user_id == user_id)
        .order_by(Interaction.created_at.desc())
        .limit(20)
    )
    interactions = result.scalars().all()

    if not interactions:
        return await get_or_create_preferences(db, user_id)

    formatted = []
    for i in interactions:
        entry = f"Q: {i.question}"
        if i.passage_text:
            entry = f"Passage: {i.passage_text[:200]}...\n{entry}"
        entry += f"\nA: {i.answer[:300]}"
        formatted.append(entry)

    interactions_text = "\n---\n".join(formatted)
    prompt = DISTILL_PROMPT.format(interactions=interactions_text)

    raw = await generate(prompt)

    try:
        json_match = raw
        if "```" in raw:
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
            if match:
                json_match = match.group(1)
        data = json.loads(json_match)
    except (json.JSONDecodeError, AttributeError):
        print(f"[distiller] Failed to parse LLM response as JSON: {raw[:200]}")
        return await get_or_create_preferences(db, user_id)

    prefs = await get_or_create_preferences(db, user_id)
    valid_styles = {"formal", "conversational", "socratic", "narrative", "balanced"}
    valid_depth = {"shallow", "moderate", "deep"}
    valid_analogy = {"low", "moderate", "high"}
    valid_math = {"avoid", "moderate", "fluent"}
    valid_pacing = {"concise", "moderate", "detailed"}

    if data.get("explanation_style") in valid_styles:
        prefs.explanation_style = data["explanation_style"]
    if data.get("depth_preference") in valid_depth:
        prefs.depth_preference = data["depth_preference"]
    if data.get("analogy_affinity") in valid_analogy:
        prefs.analogy_affinity = data["analogy_affinity"]
    if data.get("math_comfort") in valid_math:
        prefs.math_comfort = data["math_comfort"]
    if data.get("pacing") in valid_pacing:
        prefs.pacing = data["pacing"]
    if data.get("meta_notes"):
        prefs.meta_notes = str(data["meta_notes"])

    prefs.interaction_count = len(interactions)
    prefs.last_distilled_at = datetime.utcnow()
    prefs.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(prefs)
    print(f"[distiller] Distilled: style={prefs.explanation_style}, depth={prefs.depth_preference}, meta={prefs.meta_notes[:80]}")
    return prefs


async def should_auto_distill(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Check if enough new interactions have accumulated to trigger auto-distillation."""
    prefs = await get_or_create_preferences(db, user_id)
    last = prefs.last_distilled_at

    if last is None:
        count = await db.scalar(
            select(func.count()).select_from(Interaction).where(Interaction.user_id == user_id)
        )
    else:
        count = await db.scalar(
            select(func.count()).select_from(Interaction).where(
                Interaction.user_id == user_id,
                Interaction.created_at > last,
            )
        )
    return (count or 0) >= 10
