"""HTTP read APIs persona consumes — brain state, sessions, profile, graph."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts import (
    BrainStateDTO,
    ConceptDTO,
    InteractionDTO,
    UserProfileDTO,
)
from infra.db import get_db
from silicon_brain.knowledge import get_concepts, get_graph_context
from silicon_brain.models.interaction import Interaction
from silicon_brain.state import get_brain_state
from silicon_brain.user_profile import (
    boost_query_embedding,
    get_user_profile,
)


router = APIRouter()


@router.get("/brain-state", response_model=BrainStateDTO)
async def brain_state(
    concept_limit: int = Query(30, ge=1, le=200),
    session_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    state = await get_brain_state(db, user_id, session_id=session_id, concept_limit=concept_limit)
    return BrainStateDTO(
        self_description=state.self_description,
        profile=UserProfileDTO.model_validate(state.profile) if state.profile else None,
        concept_nodes=[ConceptDTO.model_validate(c) for c in state.concept_nodes],
        graph_context=state.graph_context,
    )


@router.get("/sessions/{session_id}/interactions", response_model=list[InteractionDTO])
async def session_interactions(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    """Chronological session history for the teacher's prompt."""
    stmt = (
        select(Interaction)
        .where(Interaction.user_id == user_id, Interaction.session_id == session_id)
        .order_by(Interaction.created_at.asc())
    )
    result = await db.execute(stmt)
    return [InteractionDTO.model_validate(i) for i in result.scalars().all()]


@router.get("/user-profile-state", response_model=UserProfileDTO)
async def user_profile_state(
    session_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    state = await get_user_profile(db, user_id, session_id=session_id)
    return UserProfileDTO.model_validate(state)


class _GraphContextResponse(BaseModel):
    context: str


@router.get("/graph-context", response_model=_GraphContextResponse)
async def graph_context(
    concepts: list[str] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    if not concepts:
        return _GraphContextResponse(context="")
    ctx = await get_graph_context(db, user_id, concepts)
    return _GraphContextResponse(context=ctx or "")


@router.get("/concepts-list", response_model=list[ConceptDTO])
async def concepts_list(
    limit: int = Query(30, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    """Persona-flavored concept feed (raw ConceptNodes, mastery left for the
    consumer to compute). The existing /api/concepts endpoint pre-computes
    mastery with HLR; this one is for callers that want the raw nodes."""
    nodes = await get_concepts(db, user_id, limit=limit)
    return [ConceptDTO.model_validate(n) for n in nodes]


class _BoostRequest(BaseModel):
    query_embedding: list[float]


class _BoostResponse(BaseModel):
    boosted: list[float]


@router.post("/preferences/boost-embedding", response_model=_BoostResponse)
async def boost_embedding(
    body: _BoostRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    boosted = await boost_query_embedding(db, user_id, body.query_embedding)
    return _BoostResponse(boosted=boosted)
