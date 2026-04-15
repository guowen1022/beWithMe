from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.silicon_brain.user_profile import distill_preferences, get_or_create_preferences
from app.api.deps import get_current_user_id

router = APIRouter()


class PreferencesRead(BaseModel):
    explanation_style: str
    depth_preference: str
    analogy_affinity: str
    math_comfort: str
    pacing: str
    meta_notes: str
    interaction_count: int
    last_distilled_at: Optional[datetime]
    has_preference_embedding: bool = False

    model_config = {"from_attributes": True}


class PreferencesUpdate(BaseModel):
    explanation_style: Optional[str] = None
    depth_preference: Optional[str] = None
    analogy_affinity: Optional[str] = None
    math_comfort: Optional[str] = None
    pacing: Optional[str] = None
    meta_notes: Optional[str] = None


@router.get("/preferences", response_model=PreferencesRead)
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    prefs = await get_or_create_preferences(db, user_id)
    data = PreferencesRead.model_validate(prefs)
    data.has_preference_embedding = prefs.preference_embedding is not None
    return data


@router.put("/preferences", response_model=PreferencesRead)
async def update_preferences(
    body: PreferencesUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    prefs = await get_or_create_preferences(db, user_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(prefs, field, value)
    prefs.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(prefs)
    return PreferencesRead.model_validate(prefs)


@router.post("/preferences/distill", response_model=PreferencesRead)
async def trigger_distill(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    prefs = await distill_preferences(db, user_id)
    return PreferencesRead.model_validate(prefs)
