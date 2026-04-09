from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.interaction import Interaction
from app.schemas.query import InteractionRead

router = APIRouter()


@router.get("/interactions", response_model=list[InteractionRead])
async def list_interactions(
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Interaction).order_by(Interaction.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return [InteractionRead.model_validate(i) for i in result.scalars().all()]
