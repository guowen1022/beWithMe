"""Upsert API for session summaries (write-side for persona)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts import SessionSummaryUpsertDTO, SummaryDTO
from infra.db import get_db
from silicon_brain.models.session_summary import SessionSummary


router = APIRouter()


@router.post("/sessions/summaries", response_model=SummaryDTO)
async def upsert_session_summary(
    body: SessionSummaryUpsertDTO,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    """Upsert by (user_id, session_id) UNIQUE constraint."""
    result = await db.execute(
        select(SessionSummary).where(
            SessionSummary.user_id == user_id,
            SessionSummary.session_id == body.session_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = SessionSummary(
            user_id=user_id,
            session_id=body.session_id,
            file_path=body.file_path,
            labels=body.labels,
            embedding=body.embedding,
        )
        db.add(row)
    else:
        row.file_path = body.file_path
        row.labels = body.labels
        if body.embedding is not None:
            row.embedding = body.embedding

    await db.commit()
    await db.refresh(row)

    return SummaryDTO(
        session_id=row.session_id,
        file_path=row.file_path,
        similarity=None,
        content="",
    )
