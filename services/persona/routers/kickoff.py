"""Kickoff realization (PR-5).

The Maestro long instance emits `maestro_long.kickoff_decision` AND fires
this webhook on ACT — the persona side takes the candidates list and
writes K inbox proposals via the existing `write_to_inbox` tool.

Phase-0 design choice: realization is code, not an LLM round-trip. The
Maestro's `opening` text already speaks the candidate clearly enough to
be the proposal's prose. A later PR can add an LLM refinement step
between candidate and inbox card if A/B data justifies it.

The handler is idempotent on (kickoff_event_id, candidate_idx) — re-firing
the webhook (e.g. after a Maestro retry) doesn't duplicate proposals.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from infra.db import async_session
from silicon_brain.models.inbox_proposal import InboxProposal
from tools.write_to_inbox import write_to_inbox


router = APIRouter()


class KickoffRequest(BaseModel):
    """Body shape the Maestro POSTs."""

    kickoff_event_id: UUID
    user_id: UUID
    candidates: list[dict[str, Any]]


def _parse_user_id(x_user_id: Optional[str]) -> UUID:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing X-User-Id")
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid X-User-Id")


async def _already_realized(
    user_id: UUID, kickoff_event_id: UUID, candidate_idx: int,
) -> bool:
    """Lookup any prior inbox_proposal with this (user, kickoff, idx)
    so re-firing the webhook is idempotent."""
    async with async_session() as db:
        existing = await db.execute(
            select(InboxProposal).where(
                InboxProposal.user_id == user_id,
                InboxProposal.kickoff_event_id == kickoff_event_id,
                InboxProposal.candidate_idx == candidate_idx,
            )
        )
        return existing.scalar_one_or_none() is not None


@router.post("/agent/kickoff")
async def realize_kickoff(
    body: KickoffRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> dict:
    # Auth: trust the shell-supplied X-User-Id, but only allow the call
    # for the same user the body claims. Belt-and-suspenders so a
    # mis-routed webhook can't write to a different learner's inbox.
    header_user_id = _parse_user_id(x_user_id)
    if header_user_id != body.user_id:
        raise HTTPException(status_code=403, detail="user mismatch")

    if not body.candidates:
        return {"written": 0, "skipped": 0}

    async def _write_one(idx: int, raw: dict) -> dict:
        if await _already_realized(body.user_id, body.kickoff_event_id, idx):
            return {"idx": idx, "skipped": True}
        result = await write_to_inbox(
            user_id=body.user_id,
            kickoff_event_id=body.kickoff_event_id,
            candidate_idx=idx,
            title=raw.get("title") or f"Suggestion {idx + 1}",
            persona_purpose=raw.get("persona_purpose") or "teacher:long-horizon-propose",
            posture=raw.get("posture") or "steady",
            opening=raw.get("opening") or "",
            body=raw,
        )
        return {"idx": idx, "result": result}

    # Parallel realization — the SPEC explicitly calls for "K parallel
    # write_to_inbox calls."
    results = await asyncio.gather(
        *[_write_one(idx, c) for idx, c in enumerate(body.candidates)],
        return_exceptions=False,
    )
    written = sum(1 for r in results if r.get("result"))
    skipped = sum(1 for r in results if r.get("skipped"))
    return {"written": written, "skipped": skipped, "results": results}
