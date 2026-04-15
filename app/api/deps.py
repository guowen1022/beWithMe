"""FastAPI dependencies for user resolution."""
from uuid import UUID
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.silicon_brain.models.user import User


async def get_current_user_id(
    x_user_id: UUID = Header(...),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Resolve user_id from the X-User-Id header and verify the user exists.

    Raises 401 if the user is not found so clients can clear stale session IDs
    (e.g. after a DB reseed) and bounce back to the user selector.
    """
    result = await db.execute(select(User.id).where(User.id == x_user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=401, detail="unknown_user")
    return x_user_id
