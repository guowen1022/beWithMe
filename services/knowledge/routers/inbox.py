"""Inbox proposal surface (PR-5).

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
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts.inbox import InboxProposalCreate, InboxProposalDTO
from infra.db import get_db
from silicon_brain.models.inbox_proposal import InboxProposal


router = APIRouter()


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
