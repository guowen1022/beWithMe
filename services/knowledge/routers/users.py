"""User management endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.silicon_brain.models.user import User

router = APIRouter()


class CreateUserRequest(BaseModel):
    username: str


class UserResponse(BaseModel):
    id: UUID
    username: str
    created_at: str

    class Config:
        from_attributes = True


@router.post("/users", response_model=UserResponse)
async def create_user(body: CreateUserRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(username=body.username)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse(id=user.id, username=user.username, created_at=user.created_at.isoformat())


@router.get("/users", response_model=list[UserResponse])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return [UserResponse(id=u.id, username=u.username, created_at=u.created_at.isoformat()) for u in users]
