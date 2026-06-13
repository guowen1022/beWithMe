"""Feed-candidate store surface — the persona-agnostic card store.

Routes (mounted under `/api` in services/knowledge/main.py):

  POST   /api/feed-candidates              — write one card
  POST   /api/feed-candidates/replace      — replace one persona's active batch
  GET    /api/feed-candidates              — list this user's cards
  POST   /api/feed-candidates/{id}/select  — user picked this card
  POST   /api/feed-candidates/{id}/dismiss — user dismissed this card

`user_id` comes from X-User-Id on every call. Status transitions move
forward only: active → selected OR active → dismissed. The store is
written by persona producers (via SiliconBrainClient) and read/mutated
by the Maestro feed engine. Modeled on the inbox-proposal router:

- Every transition emits a stream event (user.card_selected,
  user.card_dismissed, system.card_expired) for the feed-interaction
  signal (future saturation reads it). Best-effort; the row is truth.
- POST enforces a PER-PERSONA stock cap (M). Over cap, the oldest active
  cards for that persona expire.
- GET sweeps TTL-expired cards before returning.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts.feed import (
    FeedCandidateCreate,
    FeedCandidateDTO,
    FeedCandidateReplace,
)
from infra.db import get_db
from silicon_brain.models.event import Event
from silicon_brain.models.feed_candidate import FeedCandidate


# Per-persona stock cap + TTL.
STOCK_CAP = 8
TTL_HOURS = 24

router = APIRouter()


async def _emit_event(
    db: AsyncSession, user_id: UUID, kind: str, source: str, body: dict,
) -> None:
    """Append one event in the same session as the row write."""
    try:
        db.add(Event(user_id=user_id, source=source, kind=kind, body=body))
        await db.flush()
    except Exception:
        pass


def _interaction_body(row: FeedCandidate) -> dict:
    return {
        "candidate_id": str(row.id),
        "source_persona": row.source_persona,
        "purpose": row.purpose,
        "posture": row.posture,
        "category": row.category,
    }


def _to_dto(row: FeedCandidate) -> FeedCandidateDTO:
    return FeedCandidateDTO(
        id=row.id,
        user_id=row.user_id,
        source_persona=row.source_persona,
        purpose=row.purpose,
        posture=row.posture,
        title=row.title,
        opening=row.opening,
        intra_rank=row.intra_rank,
        category=row.category,
        body=dict(row.body) if row.body is not None else None,
        status=row.status,
        created_at=row.created_at,
        selected_at=row.selected_at,
        expires_at=row.expires_at,
    )


async def _expire_over_cap(db: AsyncSession, user_id: UUID, source_persona: str) -> int:
    """Keep at most STOCK_CAP active cards per (user, persona); expire the
    oldest beyond that."""
    active = list((await db.execute(
        select(FeedCandidate)
        .where(
            FeedCandidate.user_id == user_id,
            FeedCandidate.source_persona == source_persona,
            FeedCandidate.status == "active",
        )
        .order_by(asc(FeedCandidate.created_at))
    )).scalars().all())
    overflow = max(0, len(active) - STOCK_CAP)
    if overflow == 0:
        return 0
    for row in active[:overflow]:
        row.status = "expired"
        await _emit_event(
            db, user_id, "system.card_expired", "system",
            {**_interaction_body(row), "reason": "stock_cap"},
        )
    return overflow


async def _expire_past_ttl(db: AsyncSession, user_id: UUID) -> int:
    """Move active cards older than TTL_HOURS to 'expired' lazily."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS)
    stale = list((await db.execute(
        select(FeedCandidate).where(
            FeedCandidate.user_id == user_id,
            FeedCandidate.status == "active",
            FeedCandidate.created_at < cutoff,
        )
    )).scalars().all())
    for row in stale:
        row.status = "expired"
        await _emit_event(
            db, user_id, "system.card_expired", "system",
            {**_interaction_body(row), "reason": "ttl"},
        )
    return len(stale)


def _row_from_create(user_id: UUID, c: FeedCandidateCreate) -> FeedCandidate:
    return FeedCandidate(
        user_id=user_id,
        source_persona=c.source_persona,
        purpose=c.purpose,
        posture=c.posture,
        title=c.title,
        opening=c.opening,
        intra_rank=c.intra_rank,
        category=c.category,
        body=c.body,
        status="active",
        expires_at=c.expires_at,
    )


@router.post("/feed-candidates", response_model=FeedCandidateDTO)
async def create_candidate(
    body: FeedCandidateCreate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = _row_from_create(user_id, body)
    db.add(row)
    await db.flush()
    await _expire_over_cap(db, user_id, body.source_persona)
    await db.commit()
    await db.refresh(row)
    return _to_dto(row)


@router.post("/feed-candidates/replace", response_model=list[FeedCandidateDTO])
async def replace_candidates(
    body: FeedCandidateReplace,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    """Atomically replace a persona's active cards with a fresh batch."""
    await db.execute(
        delete(FeedCandidate).where(
            FeedCandidate.user_id == user_id,
            FeedCandidate.source_persona == body.source_persona,
            FeedCandidate.status == "active",
        )
    )
    rows = [_row_from_create(user_id, c) for c in body.items]
    for r in rows:
        db.add(r)
    await db.flush()
    await _expire_over_cap(db, user_id, body.source_persona)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return [_to_dto(r) for r in rows if r.status == "active"]


@router.get("/feed-candidates", response_model=list[FeedCandidateDTO])
async def list_candidates(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
    status: Optional[str] = Query(default=None),
    source_persona: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    expired = await _expire_past_ttl(db, user_id)
    if expired:
        await db.commit()
    stmt = select(FeedCandidate).where(FeedCandidate.user_id == user_id)
    if status:
        stmt = stmt.where(FeedCandidate.status == status)
    if source_persona:
        stmt = stmt.where(FeedCandidate.source_persona == source_persona)
    stmt = stmt.order_by(desc(FeedCandidate.created_at)).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dto(r) for r in rows]


@router.get("/feed-candidates/users", response_model=list[str])
async def list_feed_user_ids(
    db: AsyncSession = Depends(get_db),
):
    """Distinct user_ids that have any feed-candidate row. Internal — the Maestro
    scheduler uses it to know whose feed to keep warm. Not user-scoped (it's an
    enumeration), so it takes no X-User-Id."""
    rows = (await db.execute(
        select(FeedCandidate.user_id).distinct()
    )).scalars().all()
    return [str(u) for u in rows]


async def _load(db: AsyncSession, candidate_id: UUID, user_id: UUID) -> FeedCandidate:
    row = (await db.execute(
        select(FeedCandidate).where(
            FeedCandidate.id == candidate_id,
            FeedCandidate.user_id == user_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="feed candidate not found")
    return row


@router.post("/feed-candidates/{candidate_id}/select", response_model=FeedCandidateDTO)
async def select_candidate(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = await _load(db, candidate_id, user_id)
    if row.status == "active":
        row.status = "selected"
        row.selected_at = datetime.now(timezone.utc)
        await _emit_event(
            db, user_id, "user.card_selected", "user", _interaction_body(row),
        )
        await db.commit()
        await db.refresh(row)
    elif row.status == "selected":
        pass  # idempotent
    else:
        raise HTTPException(
            status_code=409, detail=f"cannot select from status {row.status!r}",
        )
    return _to_dto(row)


@router.post("/feed-candidates/{candidate_id}/dismiss", response_model=FeedCandidateDTO)
async def dismiss_candidate(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = await _load(db, candidate_id, user_id)
    if row.status == "active":
        row.status = "dismissed"
        await _emit_event(
            db, user_id, "user.card_dismissed", "user", _interaction_body(row),
        )
        await db.commit()
        await db.refresh(row)
    elif row.status == "dismissed":
        pass
    else:
        raise HTTPException(
            status_code=409, detail=f"cannot dismiss from status {row.status!r}",
        )
    return _to_dto(row)
