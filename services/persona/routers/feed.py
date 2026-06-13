"""Persona feed-production route.

`POST /api/agent/produce-candidates` runs the teacher's intra-source
producer (slow — LLM reasoning over mastery/graph/profile) and publishes
cards into the shared `feed_candidates` store. The Maestro fires this
fire-and-forget when the feed is stale; it can also be called explicitly
(the launcher's "Prepare new options" routes through the Maestro, which
calls here). Kept OFF the feed-open path so opening the feed stays fast.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.db import get_db
from infra.silicon_brain_client import SiliconBrainClient
from persona.teacher.feed.producer import produce_teacher_feed, SOURCE_PERSONA


router = APIRouter(tags=["feed"])


def _get_client(request: Request) -> SiliconBrainClient:
    client = getattr(request.app.state, "brain_client", None)
    if client is None:
        client = SiliconBrainClient()
        request.app.state.brain_client = client
    return client


@router.post("/agent/produce-candidates")
async def produce_candidates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(parse_user_id),
) -> dict:
    """Generate the teacher's feed cards and replace its active batch."""
    client = _get_client(request)
    cards = await produce_teacher_feed(db, user_id, client)
    return {"produced": len(cards), "source_persona": SOURCE_PERSONA}
