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

from infra.topology import service_port
from persona.teacher.silicon_brain_client import SiliconBrainClient

from services.persona.routers import (
    ask as ask_router,
    goals as goals_router,
    interactions as interactions_router,
    recommender as recommender_router,
    sessions as sessions_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.brain_client = SiliconBrainClient()
    try:
        yield
    finally:
        await app.state.brain_client.aclose()


app = FastAPI(title="beWithMe persona", lifespan=lifespan)
app.include_router(ask_router.router, prefix="/api")
app.include_router(interactions_router.router, prefix="/api")
app.include_router(recommender_router.router, prefix="/api")
app.include_router(goals_router.router, prefix="/api")
app.include_router(sessions_router.router, prefix="/api")


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
