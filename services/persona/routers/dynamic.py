"""Dynamic UI back-channel — SSE multiplexer + push/error endpoints.

Three endpoints:
  GET  /api/dynamic/stream             — SSE channel keyed by X-User-Id.
                                          Carries UIUpdate, BlockMessage,
                                          BlockError events for that user.
  POST /api/dynamic/push/{block_id}    — body {topic, value}; fans out a
                                          BlockMessage to the user's stream.
  POST /api/dynamic/error/{block_id}   — frontend reports browser-side eval
                                          failures here; fans out a BlockError.

The registry is in-memory (one asyncio.Queue per active user-stream) and
shared with `tools/request_ui_block.py` via `enqueue_for_user`. No DB
persistence in v1 — if the user reloads, mounted blocks vanish until the
teacher re-issues `/block <description>`.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from infra.auth import parse_user_id as get_current_user_id
from infra.contracts.ui import BlockError, BlockMessage, BlockSource, UIUpdate

from agents.frontend_engineer import llm_engineer

router = APIRouter()


# user_id (str) → set of queues. One queue per open SSE connection.
# A single user with two windows open gets two queues; both receive every
# event. We fan-out to all active queues for a user_id.
_subscribers: dict[str, Set[asyncio.Queue]] = defaultdict(set)


def _serialize(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def enqueue_for_user(user_id: UUID, event: BaseModel) -> int:
    """Fan an event out to every queue for this user. Returns the count."""
    key = str(user_id)
    queues = list(_subscribers.get(key, ()))
    payload = _serialize(event)
    for q in queues:
        await q.put(payload)
    return len(queues)


@router.get("/dynamic/stream")
async def dynamic_stream(
    request: Request,
    user_id: UUID = Depends(get_current_user_id),
):
    """SSE channel for one user. Stays open until the client disconnects."""
    queue: asyncio.Queue = asyncio.Queue()
    key = str(user_id)
    _subscribers[key].add(queue)

    async def gen():
        try:
            # Initial hello — proves the channel is open even before any
            # block ships. Frontend ignores any event with type != known.
            yield "data: {\"type\":\"open\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    # SSE keepalive comment — keeps proxies and idle TCP
                    # paths from closing the stream.
                    yield ": keepalive\n\n"
        finally:
            _subscribers[key].discard(queue)
            if not _subscribers[key]:
                _subscribers.pop(key, None)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class _PushBody(BaseModel):
    topic: str
    value: Any = None


@router.post("/dynamic/push/{block_id}")
async def dynamic_push(
    block_id: str,
    body: _PushBody,
    user_id: UUID = Depends(get_current_user_id),
):
    """Send a value to a topic on a mounted block."""
    event = BlockMessage(block_id=block_id, topic=body.topic, value=body.value)
    delivered = await enqueue_for_user(user_id, event)
    if delivered == 0:
        # Soft signal — caller may have raced the SSE connection. The block
        # bus is sticky on the client so a slightly-late publish still wins,
        # but with no open stream we have no client to publish to.
        raise HTTPException(status_code=409, detail="no active dynamic stream")
    return {"delivered_to": delivered}


class _ErrorBody(BaseModel):
    error: str


@router.post("/dynamic/error/{block_id}")
async def dynamic_error(
    block_id: str,
    body: _ErrorBody,
    user_id: UUID = Depends(get_current_user_id),
):
    """Frontend reports a browser-side eval/run failure for a block."""
    event = BlockError(block_id=block_id, error=body.error)
    await enqueue_for_user(user_id, event)
    print(f"[dynamic] block_error user={user_id} block={block_id} err={body.error!r}", flush=True)
    return {"ok": True}


@router.get("/dynamic/canvas")
async def dynamic_canvas(
    user_id: UUID = Depends(get_current_user_id),
) -> list[BlockSource]:
    """Return every block currently in the user's per-user-git workspace.

    Used by the /canvas page to hydrate the dynamic surface on first load —
    so blocks survive page reloads. The list mirrors what mount events
    would deliver if the user re-issued every command they've ever run.
    """
    return llm_engineer.list_blocks(user_id)
