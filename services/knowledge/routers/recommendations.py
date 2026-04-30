"""silicon_brain-side write API for the recommender. Persona calls these."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts import RecommendationCreateDTO, RecommendationDTO
from infra.db import get_db
from silicon_brain.models.recommendation import Recommendation


router = APIRouter()


@router.get("/recommendations", response_model=list[RecommendationDTO])
async def list_recommendations(
    source: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    status: str = Query("active"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    query = (
        select(Recommendation)
        .where(Recommendation.user_id == user_id, Recommendation.status == status)
        .order_by(Recommendation.priority.desc())
    )
    if source:
        query = query.where(Recommendation.source == source)
    if category:
        query = query.where(Recommendation.category == category)

    result = await db.execute(query)
    rows = result.scalars().all()
    return [RecommendationDTO.model_validate(r) for r in rows]


class _ReplaceActiveRequest(BaseModel):
    source: str  # "llm" | "web"
    recommendations: list[RecommendationCreateDTO]


@router.post("/recommendations/replace-active", response_model=list[RecommendationDTO])
async def replace_active(
    body: _ReplaceActiveRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    """Transactional: delete active recs of `source`, insert the new batch.

    Default `expires_at` = now+7d if the caller didn't supply one.
    """
    await db.execute(
        delete(Recommendation).where(
            Recommendation.user_id == user_id,
            Recommendation.source == body.source,
            Recommendation.status == "active",
        )
    )

    default_expiry = datetime.now(timezone.utc) + timedelta(days=7)
    inserted: list[Recommendation] = []
    for rec in body.recommendations:
        row = Recommendation(
            user_id=user_id,
            source=rec.source or body.source,
            category=rec.category,
            title=rec.title,
            summary=rec.summary,
            reasoning=rec.reasoning,
            concept_names=rec.concept_names,
            priority=rec.priority,
            status="active",
            expires_at=rec.expires_at or default_expiry,
            url=rec.url,
        )
        db.add(row)
        inserted.append(row)

    await db.commit()
    for row in inserted:
        await db.refresh(row)
    return [RecommendationDTO.model_validate(r) for r in inserted]


class _StatusUpdate(BaseModel):
    status: str  # "active" | "dismissed" | "accepted"


@router.patch("/recommendations/{recommendation_id}/status", response_model=RecommendationDTO)
async def update_status(
    recommendation_id: UUID,
    body: _StatusUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    if body.status not in ("active", "dismissed", "accepted"):
        raise HTTPException(status_code=400, detail="invalid status")

    result = await db.execute(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.user_id == user_id,
        )
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="not found")

    rec.status = body.status
    await db.commit()
    await db.refresh(rec)
    return RecommendationDTO.model_validate(rec)
