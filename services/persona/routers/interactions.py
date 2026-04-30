import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from infra.db import get_db
from persona.teacher.models.interaction import Interaction
from persona.teacher.schemas import InteractionRead
from persona.teacher.brain_builder.background import post_interaction_update
from persona.teacher.schemas import SignalRequest
from infra.auth import parse_user_id as get_current_user_id

router = APIRouter()

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()


@router.get("/interactions", response_model=list[InteractionRead])
async def list_interactions(
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    stmt = (
        select(Interaction)
        .where(Interaction.user_id == user_id)
        .order_by(Interaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [InteractionRead.model_validate(i) for i in result.scalars().all()]


@router.post("/interactions/signal")
async def record_signal(
    body: SignalRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Record a block-level signal (got_it / review_later) as a lightweight interaction."""
    label = "[got it]" if body.signal == "got_it" else "[review later]"
    interaction = Interaction(
        user_id=user_id,
        session_id=body.session_id,
        parent_interaction_id=body.parent_interaction_id,
        question=label,
        answer="",
        passage_text=body.block_text[:500],
    )
    db.add(interaction)
    await db.commit()
    await db.refresh(interaction)

    task = asyncio.get_event_loop().create_task(
        post_interaction_update(interaction.id, user_id)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "ok", "interaction_id": str(interaction.id)}
