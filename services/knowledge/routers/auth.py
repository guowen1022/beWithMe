"""Auth verification endpoint — the source of truth the shell consults.

The shell calls /api/auth/verify with X-User-Id; we 200 if the user exists,
401 if not. This is the only DB-backed auth check in the system. The
DB-touching dependency lives here (not in infra.auth) so infra stays
stateless and never imports from silicon_brain.
"""
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.db import get_db
from silicon_brain.models.user import User


router = APIRouter()


async def _verified_user_id(
    x_user_id: UUID = Header(...),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Resolve X-User-Id and verify the user exists. 401 if not."""
    result = await db.execute(select(User.id).where(User.id == x_user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=401, detail="unknown_user")
    return x_user_id


@router.get("/auth/verify")
async def verify(user_id: UUID = Depends(_verified_user_id)):
    """Returns 200 if X-User-Id maps to an existing user, 401 otherwise."""
    return {"ok": True, "user_id": str(user_id)}
