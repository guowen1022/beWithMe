"""Recommendation HTTP routes — served by the persona sidecar.

Recommendations are teacher's data; this router queries teacher's own DB
directly (no SiliconBrainClient roundtrip). The narrow client is only used
to fetch the user's `self_description` (silicon_brain Profile) when the LLM
generation needs it.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts import RecommendationDTO
from infra.db import get_db

from persona.teacher.models.recommendation import Recommendation
from persona.teacher.recommender.engine import (
    generate_llm_recommendations,
    generate_web_recommendations,
)
from persona.teacher.silicon_brain_client import SiliconBrainClient


router = APIRouter(tags=["recommendations"])


def _get_client(request: Request) -> SiliconBrainClient:
    client = getattr(request.app.state, "brain_client", None)
    if client is None:
        client = SiliconBrainClient()
        request.app.state.brain_client = client
    return client


@router.get("/recommendations", response_model=list[RecommendationDTO])
async def list_recommendations(
    source: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(parse_user_id),
):
    stmt = (
        select(Recommendation)
        .where(Recommendation.user_id == user_id, Recommendation.status == "active")
        .order_by(Recommendation.priority.desc())
    )
    if source:
        stmt = stmt.where(Recommendation.source == source)
    if category:
        stmt = stmt.where(Recommendation.category == category)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [RecommendationDTO.model_validate(r) for r in rows]


@router.post("/recommendations/generate", response_model=list[RecommendationDTO])
async def generate_recommendations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(parse_user_id),
):
    """Trigger recommendation regeneration (LLM + web)."""
    client = _get_client(request)

    # Fetch user's self_description from silicon_brain (neutral data).
    self_description = ""
    try:
        profile = await client.get_profile(user_id)
        self_description = profile.self_description if profile else ""
    except Exception as e:
        print(f"[recommender] could not fetch profile: {e}", flush=True)

    llm_recs = await generate_llm_recommendations(db, user_id, self_description)

    web_recs = []
    try:
        browser_context = request.app.state.browser_context
        web_recs = await generate_web_recommendations(db, user_id, browser_context, llm_recs)
    except AttributeError:
        # No Playwright in this process — skip web.
        pass
    except Exception as e:
        print(f"[recommender] Web recommendation generation failed: {e}", flush=True)

    all_recs = list(llm_recs) + list(web_recs)
    all_recs.sort(key=lambda r: r.priority, reverse=True)
    return [RecommendationDTO.model_validate(r) for r in all_recs]


@router.patch("/recommendations/{recommendation_id}", response_model=RecommendationDTO)
async def update_recommendation(
    recommendation_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(parse_user_id),
):
    """Update recommendation status (dismiss or accept)."""
    status = body.get("status")
    if status not in ("dismissed", "accepted"):
        raise HTTPException(status_code=400, detail="Status must be 'dismissed' or 'accepted'")

    result = await db.execute(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.user_id == user_id,
        )
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    rec.status = status
    await db.commit()
    await db.refresh(rec)
    return RecommendationDTO.model_validate(rec)
