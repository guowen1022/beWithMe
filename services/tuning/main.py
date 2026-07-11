"""Tuning sidecar — :BASE_PORT+8. beWithMe's skillforge host face.

Serves the REAL eval signal for `skill_menu.canvas_guides`:
`POST /eval {body, config, scenario} → {ok, quality, outcome}` (see
services/tuning/scorer.py — full canvas-writer replay + LLM judge), and
self-registers with the local skillforge instance on boot (idempotent;
see services/tuning/registration.py).

Offline-only: skillforge's refine/drift loop is the sole caller. Nothing on
the user request path touches this sidecar, so it is NOT routed through the
shell and carries no auth gate. Serving stays fail-open (the adapter in
infra/skillforge_client.py), gating stays fail-closed (scorer fail-safe
zeros + skillforge's RemoteEvalBackend).

Run standalone:
    python -m services.tuning
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from infra.config import settings
from infra.event_log_middleware import install_event_log
from infra.topology import service_port
from persona.teacher.prompts.canvas_guides import MENU_TUNABLE_ID
from services.tuning import capture, registration, scorer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Registration is fail-open: a down skillforge just means no refinement
    # runs until the next boot or a manual POST /register — the eval endpoint
    # itself serves regardless.
    try:
        summary = await asyncio.to_thread(registration.register)
        print(f"[tuning] skillforge registration: {summary}", flush=True)
    except Exception as e:
        print(f"[tuning] skillforge registration failed (fail-open): {e}", flush=True)
    yield


app = FastAPI(title="beWithMe tuning", lifespan=lifespan)
install_event_log(app, service="tuning")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "host": settings.skillforge_host,
        "tunable": MENU_TUNABLE_ID,
    }


@app.post("/eval")
async def eval_endpoint(payload: dict) -> dict:
    """The host-eval contract skillforge's RemoteEvalBackend RPCs — one call
    per (candidate, scenario). Never raises: the scorer maps every internal
    failure to {ok: false, quality: 0, outcome: 0} (fail-closed gating)."""
    return await scorer.score(
        body=payload.get("body") or "",
        config=payload.get("config") or {},
        scenario=payload.get("scenario") or {},
    )


@app.post("/register")
async def register_endpoint() -> dict:
    """Manual re-registration trigger (e.g. after restarting skillforge)."""
    try:
        return await asyncio.to_thread(registration.register)
    except Exception as e:
        return {"skipped": False, "error": str(e)}


@app.post("/capture")
async def capture_endpoint(payload: dict) -> dict:
    """beWithMe-internal: the writer fire-and-forgets a real failed menu turn
    here; we forward it to skillforge as a replayable `from_failure` scenario
    (M8). Fail-open — a down skillforge just drops the case, never the turn."""
    try:
        return await asyncio.to_thread(capture.forward_case, payload)
    except Exception as e:
        return {"captured": False, "error": str(e)}


def main() -> None:
    import uvicorn
    uvicorn.run(
        "services.tuning.main:app",
        host="0.0.0.0",
        port=service_port("tuning"),
        reload=False,
    )


if __name__ == "__main__":
    main()
