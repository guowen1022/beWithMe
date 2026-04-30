"""Auth verification endpoint — the source of truth the shell consults.

The shell calls this with X-User-Id; we 200 if the user exists, 401 if not.
This is the only DB-backed auth check in the system.
"""
from uuid import UUID
from fastapi import APIRouter, Depends
from app.auth import get_current_user_id

router = APIRouter()


@router.get("/auth/verify")
async def verify(user_id: UUID = Depends(get_current_user_id)):
    """Returns 200 if X-User-Id maps to an existing user, 401 otherwise."""
    return {"ok": True, "user_id": str(user_id)}
