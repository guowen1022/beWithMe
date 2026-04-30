"""Knowledge sidecar — :BASE_PORT+2.

Hosts every domain CRUD endpoint that wraps the silicon brain DB:
health, users, profile, preferences, concepts, sessions, documents, goals,
recommender. Talks to Postgres + Ollama. No models, no Playwright.

Run standalone:
    python -m services.knowledge
"""
from __future__ import annotations

from fastapi import FastAPI

from app.teacher.recommender.router import router as recommender_router
import app.teacher.recommender.models  # noqa: F401 — register ORM model for create_all

from services.knowledge.routers import (
    auth,
    concepts,
    documents,
    goals,
    health,
    preferences,
    profile,
    sessions,
    users,
)
from services.shell.proxy import service_port


app = FastAPI(title="beWithMe knowledge")
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(concepts.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(goals.router, prefix="/api")
app.include_router(recommender_router, prefix="/api")


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
