"""Inbox proposal surface (PR-5 + PR-7).

Routes (mounted under `/api` in services/knowledge/main.py):

  POST   /api/inbox                         — write one proposal
  GET    /api/inbox                         — list this user's proposals
  POST   /api/inbox/{id}/tap                — mark a proposal tapped
  POST   /api/inbox/{id}/dismiss            — mark a proposal dismissed
  POST   /api/inbox/{id}/consume            — engagement helper acks the
                                              cache has been seeded

`user_id` comes from X-User-Id on every call. Status transitions are
guarded: a proposal can only move forward (pending → tapped → consumed
OR pending → dismissed). Re-tapping is idempotent and returns the
existing row.

PR-7 additions:
- Every state transition emits a stream event for the
  inbox_interaction_log view (user.proposal_tapped, user.proposal_dismissed,
  user.proposal_consumed, system.proposal_expired). Best-effort: failure
  to emit doesn't break the transition; the row is the source of truth.
- POST /api/inbox enforces a per-user stock cap (M=5). When over cap,
  the OLDEST pending proposals expire (and emit system.proposal_expired)
  before the new one is returned.
- GET /api/inbox sweeps expired proposals before returning — any
  pending proposal older than TTL_HOURS (=24h) is moved to status
  'expired' lazily.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts.event import EventEmit
from infra.contracts.inbox import InboxProposalCreate, InboxProposalDTO
from infra.db import get_db
from silicon_brain.models.event import Event
from silicon_brain.models.inbox_proposal import InboxProposal


# SPEC §6.1.2: per-user inbox stock cap + TTL.
STOCK_CAP = 5
TTL_HOURS = 24


router = APIRouter()


async def _emit_event(
    db: AsyncSession,
    user_id: UUID,
    kind: str,
    source: str,
    body: dict,
) -> None:
    """Append one event directly via the ORM. Same DB session as the
    inbox row write so it commits in the same transaction."""
    try:
        ev = Event(
            user_id=user_id,
            source=source,
            kind=kind,
            body=body,
        )
        db.add(ev)
        await db.flush()  # let the caller's commit pick this up
    except Exception:
        # Observability event — don't let a stream-write failure break
        # the inbox transition.
        pass


def _interaction_body(row: InboxProposal) -> dict:
    return {
        "proposal_id": str(row.id),
        "kickoff_event_id": str(row.kickoff_event_id),
        "candidate_idx": row.candidate_idx,
        "persona_purpose": row.persona_purpose,
        "posture": row.posture,
    }


async def _expire_over_cap(db: AsyncSession, user_id: UUID) -> int:
    """If the user has more than STOCK_CAP pending proposals, expire the
    oldest until they're at-cap. Emits system.proposal_expired per row.
    Returns the count expired."""
    pending = list((await db.execute(
        select(InboxProposal)
        .where(
            InboxProposal.user_id == user_id,
            InboxProposal.status == "pending",
        )
        .order_by(asc(InboxProposal.created_at))
    )).scalars().all())
    overflow = max(0, len(pending) - STOCK_CAP)
    if overflow == 0:
        return 0
    now = datetime.now(timezone.utc)
    for row in pending[:overflow]:
        row.status = "expired"
        row.consumed_at = now
        await _emit_event(
            db, user_id, "system.proposal_expired", "system",
            {**_interaction_body(row), "reason": "stock_cap"},
        )
    return overflow


async def _expire_past_ttl(db: AsyncSession, user_id: UUID) -> int:
    """Move any pending proposal older than TTL_HOURS to status 'expired'.
    Emits system.proposal_expired per row. Returns the count expired."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS)
    pending = list((await db.execute(
        select(InboxProposal)
        .where(
            InboxProposal.user_id == user_id,
            InboxProposal.status == "pending",
            InboxProposal.created_at < cutoff,
        )
    )).scalars().all())
    if not pending:
        return 0
    now = datetime.now(timezone.utc)
    for row in pending:
        row.status = "expired"
        row.consumed_at = now
        await _emit_event(
            db, user_id, "system.proposal_expired", "system",
            {**_interaction_body(row), "reason": "ttl"},
        )
    return len(pending)


def _to_dto(row: InboxProposal) -> InboxProposalDTO:
    return InboxProposalDTO(
        id=row.id,
        user_id=row.user_id,
        kickoff_event_id=row.kickoff_event_id,
        candidate_idx=row.candidate_idx,
        title=row.title,
        persona_purpose=row.persona_purpose,
        posture=row.posture,
        opening=row.opening,
        body=dict(row.body) if row.body is not None else None,
        status=row.status,
        created_at=row.created_at,
        tapped_at=row.tapped_at,
        consumed_at=row.consumed_at,
    )


@router.post("/inbox", response_model=InboxProposalDTO)
async def create_proposal(
    body: InboxProposalCreate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = InboxProposal(
        user_id=user_id,
        kickoff_event_id=body.kickoff_event_id,
        candidate_idx=body.candidate_idx,
        title=body.title,
        persona_purpose=body.persona_purpose,
        posture=body.posture,
        opening=body.opening,
        body=body.body,
        status="pending",
    )
    db.add(row)
    # SPEC §6.1.2 stock cap — after this insert, anything over M is the
    # oldest pending and gets auto-expired with a system event.
    await db.flush()
    await _expire_over_cap(db, user_id)
    await db.commit()
    await db.refresh(row)
    return _to_dto(row)


@router.get("/inbox", response_model=list[InboxProposalDTO])
async def list_proposals(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    # PR-7 TTL sweep: lazy expiry of stale pending proposals on every
    # read. Phase 0 chose lazy over cron — simpler, no extra scheduler.
    expired = await _expire_past_ttl(db, user_id)
    if expired:
        await db.commit()
    stmt = select(InboxProposal).where(InboxProposal.user_id == user_id)
    if status:
        stmt = stmt.where(InboxProposal.status == status)
    stmt = stmt.order_by(desc(InboxProposal.created_at)).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dto(r) for r in rows]


async def _load_and_authorise(
    db: AsyncSession, proposal_id: UUID, user_id: UUID,
) -> InboxProposal:
    row = (
        await db.execute(
            select(InboxProposal).where(
                InboxProposal.id == proposal_id,
                InboxProposal.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="inbox proposal not found")
    return row


@router.post("/inbox/{proposal_id}/tap", response_model=InboxProposalDTO)
async def tap_proposal(
    proposal_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = await _load_and_authorise(db, proposal_id, user_id)
    if row.status == "pending":
        row.status = "tapped"
        row.tapped_at = datetime.now(timezone.utc)
        await _emit_event(
            db, user_id, "user.proposal_tapped", "user", _interaction_body(row),
        )
        await db.commit()
        await db.refresh(row)
    elif row.status in ("tapped", "consumed"):
        # Idempotent — re-tap returns the existing row.
        pass
    else:
        raise HTTPException(
            status_code=409,
            detail=f"cannot tap from status {row.status!r}",
        )
    return _to_dto(row)


@router.post("/inbox/{proposal_id}/dismiss", response_model=InboxProposalDTO)
async def dismiss_proposal(
    proposal_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = await _load_and_authorise(db, proposal_id, user_id)
    if row.status == "pending":
        row.status = "dismissed"
        await _emit_event(
            db, user_id, "user.proposal_dismissed", "user", _interaction_body(row),
        )
        await db.commit()
        await db.refresh(row)
    elif row.status == "dismissed":
        pass
    else:
        raise HTTPException(
            status_code=409,
            detail=f"cannot dismiss from status {row.status!r}",
        )
    return _to_dto(row)


@router.post("/inbox/{proposal_id}/consume", response_model=InboxProposalDTO)
async def consume_proposal(
    proposal_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    """Internal: the engagement helper marks a tapped proposal consumed
    once it has seeded the maestro cache. Idempotent."""
    row = await _load_and_authorise(db, proposal_id, user_id)
    if row.status == "tapped":
        row.status = "consumed"
        row.consumed_at = datetime.now(timezone.utc)
        await _emit_event(
            db, user_id, "user.proposal_consumed", "user", _interaction_body(row),
        )
        await db.commit()
        await db.refresh(row)
    elif row.status == "consumed":
        pass
    else:
        raise HTTPException(
            status_code=409,
            detail=f"cannot consume from status {row.status!r}",
        )
    return _to_dto(row)
