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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from infra.contracts.event import EventDTO
from infra.event_log import log_event
from infra.event_log_middleware import install_event_log
from infra.topology import service_port

from services.maestro import long as _long
from services.maestro.cache import Cache


app = FastAPI(title="beWithMe maestro")
install_event_log(app, service="maestro")


# Process-wide cache instance. PR-5 will surface this to the persona side
# for per-LLM-call reads; PR-6 will write to it from the short instance.
_CACHE = Cache()


class WebhookRequest(BaseModel):
    """Triggering event the long instance should consider. Shape mirrors
    EventDTO so callers can pass the value they already have in hand."""

    event: EventDTO


@app.get("/api/maestro/health")
async def health() -> dict:
    return {
        "service": "maestro",
        "ok": True,
        "cache_size": await _CACHE.size(),
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
