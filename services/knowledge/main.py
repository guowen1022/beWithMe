"""Knowledge sidecar — :BASE_PORT+2.

Silicon-brain HTTP face. Hosts CRUD for users / profile / preferences / concepts
/ documents / health, plus the persona-facing read/write APIs (brain-state,
retrieval, recommendations, session-summaries, interactions, brain-builder).

Agent-driven endpoints (ask, interactions/signal, recommendations/generate,
goals, sessions/end, sessions/summaries/graph) live in services/persona/.

Run standalone:
    python -m services.knowledge
"""
from __future__ import annotations

from fastapi import FastAPI

# Register every ORM model so SQLAlchemy create_all sees them.
import silicon_brain.models  # noqa: F401

from services.knowledge.routers import (
    auth,
    brain_builder,
    brain_state,
    concepts,
    documents,
    health,
    interactions,
    preferences,
    profile,
    recommendations,
    retrieval,
    session_summaries,
    users,
)
from infra.topology import service_port


app = FastAPI(title="beWithMe knowledge")
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(concepts.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
# Persona-facing read/write APIs.
app.include_router(brain_state.router, prefix="/api")
app.include_router(retrieval.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")
app.include_router(session_summaries.router, prefix="/api")
app.include_router(interactions.router, prefix="/api")
app.include_router(brain_builder.router, prefix="/api")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.knowledge.main:app",
        host="0.0.0.0",
        port=service_port("knowledge"),
        reload=False,
    )


if __name__ == "__main__":
    main()
