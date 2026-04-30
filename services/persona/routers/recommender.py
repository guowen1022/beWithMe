"""Recommendation HTTP routes.

This router is currently mounted by services/knowledge during the migration.
Per the user, routers don't belong in persona — this file moves to
`services/persona/routers/recommender.py` in the persona-sidecar step. For now
it lives here but uses the silicon_brain HTTP client (no DB session, no ORM).
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from infra.auth import parse_user_id
from infra.contracts import RecommendationDTO
from persona.teacher.recommender.engine import (
    generate_llm_recommendations,
    generate_web_recommendations,
)
from persona.teacher.silicon_brain_client import SiliconBrainClient


router = APIRouter(tags=["recommendations"])


def _get_client(request: Request) -> SiliconBrainClient:
    """Per-request client lookup. Knowledge sidecar's lifespan creates one and
    stashes it on app.state.brain_client. If absent (sidecar not yet wired),
    fall back to a fresh per-call client."""
    client = getattr(request.app.state, "brain_client", None)
    if client is None:
        client = SiliconBrainClient()
        request.app.state.brain_client = client
    return client


@router.get("/recommendations", response_model=list[RecommendationDTO])
async def list_recommendations(
    request: Request,
    source: Optional[str] = None,
    category: Optional[str] = None,
    user_id: uuid.UUID = Depends(parse_user_id),
):
    client = _get_client(request)
    return await client.list_recommendations(user_id, source=source, category=category)


@router.post("/recommendations/generate", response_model=list[RecommendationDTO])
async def generate_recommendations(
    request: Request,
    user_id: uuid.UUID = Depends(parse_user_id),
):
    """Trigger recommendation regeneration (LLM + web)."""
    client = _get_client(request)

    llm_recs = await generate_llm_recommendations(user_id, client)

    web_recs: list[RecommendationDTO] = []
    try:
        browser_context = request.app.state.browser_context
        web_recs = await generate_web_recommendations(user_id, browser_context, client, llm_recs)
    except AttributeError:
        # No Playwright in this process (e.g. knowledge sidecar) — skip web.
        pass
    except Exception as e:
        print(f"[recommender] Web recommendation generation failed: {e}", flush=True)

    all_recs = list(llm_recs) + list(web_recs)
    all_recs.sort(key=lambda r: r.priority, reverse=True)
    return all_recs


@router.patch("/recommendations/{recommendation_id}", response_model=RecommendationDTO)
async def update_recommendation(
    recommendation_id: uuid.UUID,
    body: dict,
    request: Request,
    user_id: uuid.UUID = Depends(parse_user_id),
):
    """Update recommendation status (dismiss or accept)."""
    status = body.get("status")
    if status not in ("dismissed", "accepted"):
        raise HTTPException(status_code=400, detail="Status must be 'dismissed' or 'accepted'")

    client = _get_client(request)
    return await client.update_recommendation_status(user_id, recommendation_id, status)
