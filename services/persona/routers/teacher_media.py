"""Debug HTTP wrapper around `workshop.canvas.tools.list_media.list_media`.

The tool itself is callable from Python (and from a future LLM tool loop).
This route exists so a developer can curl/browse the inventory without
spinning up the teacher's tool-calling stack.

Mounted under the `dynamic` prefix so the shell's existing route table
forwards it to the persona sidecar without a topology change.

GET /api/dynamic/media → MediaInventory (auth: X-User-Id).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from infra.auth import parse_user_id as get_current_user_id
from infra.contracts.devices import MediaInventory

from workshop.canvas.tools.list_media import list_media

router = APIRouter()


@router.get("/dynamic/media", response_model=MediaInventory)
async def teacher_media(
    user_id: UUID = Depends(get_current_user_id),
) -> MediaInventory:
    return await list_media(user_id)
