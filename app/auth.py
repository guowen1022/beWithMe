"""Auth deps for the shared library.

Two flavors:

  - `parse_user_id` (no DB)  — used by every sidecar route. Parses the
    `X-User-Id` header that the **shell** has already verified. Sidecars do
    not re-check the DB, since auth is centralized at the gateway.

  - `get_current_user_id` (DB-backed) — used only by the knowledge sidecar's
    `/api/auth/verify` endpoint, which is the source of truth the shell
    consults. Keeps the existing 401 semantics: "unknown_user" if the row
    does not exist (e.g. after a DB reseed), so clients clear stale session
    ids.

Network model: sidecars trust X-User-Id verbatim. They MUST only be reachable
on the same private network as the shell — never expose them publicly.
"""
from uuid import UUID
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.silicon_brain.models.user import User


async def parse_user_id(x_user_id: UUID = Header(...)) -> UUID:
    """Trust the shell — return the UUID parsed from X-User-Id, no DB check."""
    return x_user_id


async def get_current_user_id(
    x_user_id: UUID = Header(...),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """DB-backed verification — the shell calls this via /api/auth/verify."""
    result = await db.execute(select(User.id).where(User.id == x_user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=401, detail="unknown_user")
    return x_user_id
