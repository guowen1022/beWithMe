"""API endpoints for the recommendation system."""
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth import parse_user_id as get_current_user_id
from app.teacher.recommender.models import Recommendation
from app.teacher.recommender.schemas import RecommendationRead, RecommendationUpdate
from app.teacher.recommender.engine import generate_llm_recommendations, generate_web_recommendations

router = APIRouter(tags=["recommendations"])


@router.get("/recommendations", response_model=list[RecommendationRead])
async def list_recommendations(
    source: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """List active recommendations for the current user."""
    query = (
        select(Recommendation)
        .where(Recommendation.user_id == user_id, Recommendation.status == "active")
        .order_by(Recommendation.priority.desc())
    )
    if source:
        query = query.where(Recommendation.source == source)
    if category:
        query = query.where(Recommendation.category == category)

    result = await db.execute(query)
    rows = result.scalars().all()
    return [RecommendationRead.model_validate(r) for r in rows]


@router.post("/recommendations/generate", response_model=list[RecommendationRead])
async def generate_recommendations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Trigger recommendation regeneration (LLM + web)."""
    # Step 1: LLM-based recommendations
    llm_recs = await generate_llm_recommendations(db, user_id)

    # Step 2: Web-based recommendations (uses browser context from app state)
    web_recs = []
    try:
        browser_context = request.app.state.browser_context
        web_recs = await generate_web_recommendations(db, user_id, browser_context, llm_recs)
    except Exception as e:
        print(f"[recommender] Web recommendation generation failed: {e}", flush=True)

    await db.commit()

    all_recs = llm_recs + web_recs
    all_recs.sort(key=lambda r: r.priority, reverse=True)
    return [RecommendationRead.model_validate(r) for r in all_recs]


@router.patch("/recommendations/{recommendation_id}", response_model=RecommendationRead)
async def update_recommendation(
    recommendation_id: uuid.UUID,
    body: RecommendationUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Update recommendation status (dismiss or accept)."""
    if body.status not in ("dismissed", "accepted"):
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

    rec.status = body.status
    await db.commit()
    await db.refresh(rec)
    return RecommendationRead.model_validate(rec)
