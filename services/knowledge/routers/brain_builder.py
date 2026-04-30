"""Brain-builder write API. Persona fires post-interaction learning here."""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from infra.auth import parse_user_id
from silicon_brain.brain_builder.background import post_interaction_update


router = APIRouter()

# Hold references so the event loop doesn't garbage-collect spawned tasks.
_background_tasks: set = set()


class _PostInteractionRequest(BaseModel):
    interaction_id: UUID


@router.post("/brain-builder/post-interaction")
async def post_interaction(
    body: _PostInteractionRequest,
    user_id: UUID = Depends(parse_user_id),
):
    """Schedule the brain-builder pipeline for a freshly written interaction.

    Fire-and-forget — returns immediately. Persona's ask handler calls this
    after streaming the answer to the user.
    """
    task = asyncio.get_event_loop().create_task(
        post_interaction_update(body.interaction_id, user_id)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "scheduled", "interaction_id": str(body.interaction_id)}
