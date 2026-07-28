"""Auth endpoints — the source of truth the shell consults.

Two endpoints, both DB-backed (the DB-touching dependency lives here, not in
infra.auth, so infra stays stateless and never imports from silicon_brain):

  * `GET  /auth/verify`  — the shell's per-user existence check. 200 if the
    user exists, 401 if not.
  * `POST /auth/session` — exchanges credentials for a signed session token
    (infra/session_token.py). This is what makes strict mode possible: the
    shell can then trust an identity instead of taking a header's word for it.

See docs/SECURITY.md for the mode model.
"""
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra import session_token
from infra.config import settings
from infra.db import get_db
from silicon_brain.models.user import User


router = APIRouter()


def _strict() -> bool:
    return (settings.bewithme_auth_mode or "legacy").strip().lower() == "strict"


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


class SessionRequest(BaseModel):
    user_id: UUID
    # Required in strict mode; ignored in legacy mode, where there is no
    # credential to check in the first place.
    access_key: str | None = None


class SessionResponse(BaseModel):
    token: str
    user_id: str
    expires_in: int


@router.post("/auth/session", response_model=SessionResponse)
async def create_session(
    body: SessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange credentials for a signed session token.

    strict: `access_key` must equal BEWITHME_ACCESS_KEY. Compared in constant
            time so a timing side channel cannot recover it byte by byte.
    legacy: no credential exists to check, so this issues a token for any real
            user — exactly the trust level the header-only path already had.
            It is here so clients can adopt tokens before the switch to strict.

    The failure response is identical for "no such user" and "wrong key": a
    caller must not be able to use this endpoint to enumerate valid user ids.
    """
    if _strict():
        expected = (settings.bewithme_access_key or "").strip()
        provided = (body.access_key or "").strip()
        if not expected or not hmac.compare_digest(expected, provided):
            raise HTTPException(status_code=401, detail="invalid_credentials")

    exists = await db.execute(select(User.id).where(User.id == body.user_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")

    try:
        token = session_token.issue(str(body.user_id))
    except session_token.TokenError as exc:
        # Only reachable when BEWITHME_SECRET_KEY is unset. In strict mode the
        # shell refuses to boot in that state, so this is the legacy path.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return SessionResponse(
        token=token,
        user_id=str(body.user_id),
        expires_in=session_token.DEFAULT_TTL_SECONDS,
    )
