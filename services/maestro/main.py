"""Maestro sidecar — :BASE_PORT+6.

Webhook endpoint for the long instance. The agent layer fires
`POST /api/maestro/event` whenever a kind worth gating on lands in the
stream (engagement_ended, capture.*, due followups). The Maestro reads
the event, decides ACT vs SILENCE, generates candidates on ACT, and
emits `maestro_long.kickoff_decision` back into the stream.

The webhook is fire-and-forget from the agent's perspective — failures
on the Maestro side never propagate to the user turn. The agent has
already emitted its own boundary event; the Maestro's output is
additive observational substrate.

Run standalone:
    python -m services.maestro
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from infra.contracts.event import EventDTO
from infra.event_log import log_event
from infra.event_log_middleware import install_event_log
from infra.topology import service_port

from services.maestro import long as _long
from services.maestro import short as _short
from services.maestro.cache import Cache, CacheEntry, VALID_POSTURES


app = FastAPI(title="beWithMe maestro")
install_event_log(app, service="maestro")


# Process-wide cache instance. PR-5 will surface this to the persona side
# for per-LLM-call reads; PR-6 will write to it from the short instance.
_CACHE = Cache()


class WebhookRequest(BaseModel):
    """Triggering event the long instance should consider. Shape mirrors
    EventDTO so callers can pass the value they already have in hand."""

    event: EventDTO


class CacheSetRequest(BaseModel):
    persona_purpose: str = Field(..., min_length=1, max_length=128)
    paragraph: str = Field(..., min_length=1)
    posture: str = "steady"
    candidate_idx: Optional[int] = None


def _entry_dict(entry: CacheEntry) -> dict:
    return {
        "user_id": str(entry.user_id),
        "persona_purpose": entry.persona_purpose,
        "paragraph": entry.paragraph,
        "posture": entry.posture,
        "written_at": entry.written_at.isoformat(),
        "candidate_idx": entry.candidate_idx,
    }


def _parse_user_id(x_user_id: Optional[str]) -> UUID:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing X-User-Id")
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid X-User-Id")


@app.get("/api/maestro/health")
async def health() -> dict:
    return {
        "service": "maestro",
        "ok": True,
        "cache_size": await _CACHE.size(),
    }


@app.get("/api/maestro/cache")
async def get_cache(
    persona_purpose: str,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> dict:
    """Read the cache entry for (user, persona_purpose). 404 if absent.

    The agent calls this on every LLM call so the active posture +
    paragraph ride into the prompt. PR-6's short instance is the main
    writer; PR-5 seeds it from inbox-proposal taps.
    """
    user_id = _parse_user_id(x_user_id)
    entry = await _CACHE.get(user_id, persona_purpose)
    if entry is None:
        raise HTTPException(status_code=404, detail="cache empty for this key")
    return _entry_dict(entry)


@app.post("/api/maestro/cache")
async def set_cache(
    body: CacheSetRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> dict:
    """Write/overwrite the cache entry for (user, persona_purpose).

    Internal — engagement.py POSTs here on tap consumption; the short
    instance POSTs here on refresh (PR-6).
    """
    user_id = _parse_user_id(x_user_id)
    if body.posture not in VALID_POSTURES:
        raise HTTPException(
            status_code=400,
            detail=f"posture must be one of {sorted(VALID_POSTURES)}",
        )
    entry = CacheEntry(
        user_id=user_id,
        persona_purpose=body.persona_purpose,
        paragraph=body.paragraph,
        posture=body.posture,
        written_at=datetime.now(timezone.utc),
        candidate_idx=body.candidate_idx,
    )
    await _CACHE.set(entry)
    return _entry_dict(entry)


@app.post("/api/maestro/signal")
async def post_signal(body: WebhookRequest) -> dict:
    """Short-instance entrypoint — in-engagement signal arrived.

    Decides refresh vs skip per SPEC §5.4 and writes a cache_refresh
    or skip_refresh event accordingly. Synchronous; failures bubble up
    as 500 with the exception class logged.
    """
    try:
        result = await _short.handle_signal(_CACHE, body.event)
    except Exception as exc:
        log_event(
            "maestro.handle_signal.error",
            kind=body.event.kind,
            error=repr(exc),
        )
        raise HTTPException(status_code=500, detail=f"handle_signal failed: {exc}")
    return {
        "ok": True,
        "decision": result.get("decision"),
        "new_posture": result.get("new_posture"),
    }


@app.post("/api/maestro/event")
async def post_event(body: WebhookRequest) -> dict:
    """Synchronous handler — the agent waits for the gate result.

    Synchronous because the caller (engagement.py) already runs after
    the user-facing reply has finished streaming; the few hundred ms
    extra is observability, not user-perceived latency. If this becomes
    a hot path the handler can spawn `_long.handle_event` as a background
    task and return immediately.
    """
    try:
        result = await _long.handle_event(body.event)
    except Exception as exc:
        log_event(
            "maestro.handle_event.error",
            kind=body.event.kind,
            error=repr(exc),
        )
        raise HTTPException(status_code=500, detail=f"handle_event failed: {exc}")
    return {"ok": True, "decision": result.get("decision"), "k": result.get("k", 0)}


def main() -> None:
    import uvicorn
    uvicorn.run(
        "services.maestro.main:app",
        host="0.0.0.0",
        port=service_port("maestro"),
        reload=False,
    )


if __name__ == "__main__":
    main()
