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
from services.tuning import capture, registration, scorer, scorer_grid
from services.tuning.scenarios_grid import GRID_TUNABLE_ID


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


# tunable_id → the MODULE that scores it. This sidecar serves ONE eval_url for
# the whole host (skillforge's registry is per-host), so every tunable we expose
# is dispatched from here.
#
# Modules, not bound functions: `{id: scorer.score}` would capture the function
# object at import, so any later rebinding of `scorer.score` — a monkeypatch, a
# hot-reload — would be silently ignored while the dispatch table kept calling
# the original. Resolving `.score` at call time keeps the indirection honest.
_SCORERS = {
    MENU_TUNABLE_ID: scorer,           # canvas-writer replay + LLM judge
    GRID_TUNABLE_ID: scorer_grid,      # normalize_spec well-formedness floor
}


@app.post("/eval")
async def eval_endpoint(payload: dict) -> dict:
    """The host-eval contract skillforge's RemoteEvalBackend RPCs — one call
    per (candidate, scenario). Never raises: the scorers map every internal
    failure to {ok: false, quality: 0, outcome: 0} (fail-closed gating).

    DISPATCH on `tunable_id`. A host registers exactly one eval_url, so with
    several tunables live this endpoint has to be told which decision it is
    scoring. skillforge carries `tunable_id` in the payload for exactly this
    (skillforge PR `feat/eval-payload-tunable-id`).

    An UNKNOWN tunable_id fails CLOSED rather than falling back to a scorer —
    scoring one tunable with another's scorer would produce a number that reads
    as legitimate, and a wrong-scorer result is indistinguishable from a real
    regression.

    Back-compat: a skillforge that predates that PR sends no `tunable_id`. We
    fall back to the canvas-guides scorer, which is what such a build can only
    have been asking for — it could not have gated a second tunable anyway.
    Keeps the two repos independently deployable in either order.
    """
    tunable_id = str(payload.get("tunable_id") or "").strip() or MENU_TUNABLE_ID
    module = _SCORERS.get(tunable_id)
    if module is None:
        return {"ok": False, "quality": 0.0, "outcome": 0.0,
                "reason": f"no scorer registered for tunable {tunable_id!r}"}
    return await module.score(
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
