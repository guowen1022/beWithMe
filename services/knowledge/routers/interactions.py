"""Interaction write API. Persona's ask handler calls this to record an answer."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts import InteractionCreateDTO, InteractionDTO
from infra.db import get_db
from silicon_brain.models.interaction import Interaction


router = APIRouter()


@router.post("/interactions", response_model=InteractionDTO)
async def create_interaction(
    body: InteractionCreateDTO,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    row = Interaction(
        user_id=user_id,
        session_id=body.session_id,
        parent_interaction_id=body.parent_interaction_id,
        title=body.title,
        passage_text=body.passage_text,
        question=body.question,
        answer=body.answer,
        source_document=body.source_document,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return InteractionDTO.model_validate(row)
