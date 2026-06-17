"""Persona sidecar — :BASE_PORT+1.

Hosts the agent-driven endpoints (ask, interactions, recommender, goals,
sessions). Replaces the old services/ask sidecar at the same offset.

Lifespan creates a single long-lived `SiliconBrainClient` and stashes it on
`app.state.brain_client` so every router shares one connection pool.

Run standalone:
    python -m services.persona
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from infra.event_log_middleware import install_event_log
from infra.observability import register_emit
from infra.topology import service_port
from persona.teacher import triggers as teacher_triggers
from infra.silicon_brain_client import SiliconBrainClient

# Register every model on infra.db.Base so FK constraints resolve.
# Teacher's tables FK to silicon_brain.users.id, so BOTH packages must be
# imported even though this sidecar only writes teacher's tables — SQLAlchemy
# walks the FK graph at flush() time and needs every referenced table visible.
import silicon_brain.models  # noqa: F401
import persona.teacher.models  # noqa: F401

from services.persona.routers import (
    ask as ask_router,
    concepts as concepts_router,
    dynamic as dynamic_router,
    feed as feed_router,
    goals as goals_router,
    interactions as interactions_router,
    kickoff as kickoff_router,
    perception_utterance as perception_utterance_router,
    preferences as preferences_router,
    screen_share as screen_share_router,
    sessions as sessions_router,
    skills as skills_router,
    teacher_media as teacher_media_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.brain_client = SiliconBrainClient()
    # Subscribe the event-driven teacher triggers to the perception cache.
    # Idempotent — install() returns immediately if already wired.
    teacher_triggers.install()
    # Wire LLM-call observability to the SSE fan-out so every LLM call
    # surfaces in the developer debug panel.
    from infra.devices.delivery import enqueue_for_user
    register_emit(enqueue_for_user)
    try:
        yield
    finally:
        teacher_triggers.uninstall()
        await app.state.brain_client.aclose()


app = FastAPI(title="beWithMe persona", lifespan=lifespan)
# Skip /api/dynamic/stream — it's a long-lived SSE channel; logging start
# per open is fine but we don't need it spamming alongside every request.
install_event_log(app, service="persona", skip_paths=("/api/dynamic/stream",))
app.include_router(ask_router.router, prefix="/api")
app.include_router(interactions_router.router, prefix="/api")
app.include_router(goals_router.router, prefix="/api")
app.include_router(sessions_router.router, prefix="/api")
app.include_router(concepts_router.router, prefix="/api")
app.include_router(dynamic_router.router, prefix="/api")
app.include_router(teacher_media_router.router, prefix="/api")
app.include_router(perception_utterance_router.router, prefix="/api")
app.include_router(preferences_router.router, prefix="/api")
app.include_router(screen_share_router.router, prefix="/api")
app.include_router(kickoff_router.router, prefix="/api")
app.include_router(feed_router.router, prefix="/api")
app.include_router(skills_router.router)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.persona.main:app",
        host="0.0.0.0",
        port=service_port("persona"),
        reload=False,
    )


if __name__ == "__main__":
    main()
