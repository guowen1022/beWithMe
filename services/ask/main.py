"""Ask sidecar — :BASE_PORT+1.

Owns LLM Q&A. Reads brain state, streams answers, fires brain-builder updates
in the background. No model files at import time — the LLM provider facade
lazy-imports its SDK on first call.

Run standalone:
    python -m services.ask
"""
from __future__ import annotations

from fastapi import FastAPI

from services.ask.routers import ask, interactions
from services.shell.proxy import service_port


app = FastAPI(title="beWithMe ask")
app.include_router(ask.router, prefix="/api")
app.include_router(interactions.router, prefix="/api")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.ask.main:app",
        host="0.0.0.0",
        port=service_port("ask"),
        reload=False,
    )


if __name__ == "__main__":
    main()
