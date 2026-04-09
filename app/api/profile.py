from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.profile import Profile
from app.schemas.query import ProfileRead, ProfileUpdate

router = APIRouter()


async def _get_or_create_profile(db: AsyncSession) -> Profile:
    result = await db.execute(select(Profile).limit(1))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = Profile(self_description="")
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


@router.get("/profile", response_model=ProfileRead)
async def get_profile(db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_profile(db)
    return ProfileRead(self_description=profile.self_description, created_at=profile.created_at)


@router.put("/profile", response_model=ProfileRead)
async def update_profile(body: ProfileUpdate, db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_profile(db)
    profile.self_description = body.self_description
    await db.commit()
    await db.refresh(profile)
    return ProfileRead(self_description=profile.self_description, created_at=profile.created_at)
