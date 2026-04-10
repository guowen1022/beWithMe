from typing import Optional, List
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.knowledge import ConceptNode, compute_mastery, mastery_to_state, get_graph_data
from app.api.deps import get_current_user_id

router = APIRouter()


class ConceptRead(BaseModel):
    id: UUID
    name: str
    state: str
    mastery_p: Optional[float] = None
    half_life_hours: Optional[float] = None
    encounter_count: int
    first_seen: datetime
    last_seen: datetime

    model_config = {"from_attributes": True}


@router.get("/concepts", response_model=List[ConceptRead])
async def list_concepts(
    state: Optional[str] = Query(None, description="Filter by state"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    stmt = select(ConceptNode).where(ConceptNode.user_id == user_id).order_by(ConceptNode.last_seen.desc())
    if state:
        stmt = stmt.where(ConceptNode.state == state)
    result = await db.execute(stmt)

    now = datetime.utcnow()
    concepts = []
    for c in result.scalars().all():
        ref_time = c.last_recalled_at or c.last_seen
        if ref_time and ref_time.tzinfo is not None:
            ref_time = ref_time.replace(tzinfo=None)
        hours_since = max(0, (now - ref_time).total_seconds() / 3600.0)
        p = compute_mastery(c.half_life_hours, hours_since)
        concepts.append(ConceptRead(
            id=c.id,
            name=c.name,
            state=mastery_to_state(p),
            mastery_p=round(p, 3),
            half_life_hours=c.half_life_hours,
            encounter_count=c.encounter_count,
            first_seen=c.first_seen,
            last_seen=c.last_seen,
        ))
    return concepts


@router.get("/graph")
async def get_graph(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Full graph data for visualization (nodes + edges)."""
    return await get_graph_data(db, user_id)
