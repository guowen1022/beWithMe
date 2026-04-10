from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.profile import Profile
from app.schemas.query import ProfileRead, ProfileUpdate
from app.api.deps import get_current_user_id

router = APIRouter()


async def _get_or_create_profile(db: AsyncSession, user_id: UUID) -> Profile:
    result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user_id, self_description="")
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


@router.get("/profile", response_model=ProfileRead)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    profile = await _get_or_create_profile(db, user_id)
    return ProfileRead(self_description=profile.self_description, created_at=profile.created_at)


@router.put("/profile", response_model=ProfileRead)
async def update_profile(
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    profile = await _get_or_create_profile(db, user_id)
    profile.self_description = body.self_description
    await db.commit()
    await db.refresh(profile)
    return ProfileRead(self_description=profile.self_description, created_at=profile.created_at)
