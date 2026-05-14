"""Knowledge sidecar — :BASE_PORT+2.

silicon_brain HTTP face. Serves only neutral user data — Users, Profile,
UserPreferences, Documents, the document-chunk vector search. Anything
teacher-authored (Interactions, Concepts, Recommendations, LearningSessions)
is served by the persona sidecar from teacher's own DB.

Run standalone:
    python -m services.knowledge
"""
from __future__ import annotations

from fastapi import FastAPI

# Register silicon_brain models for SQLAlchemy create_all.
import silicon_brain.models  # noqa: F401
# Also register teacher's models — they share infra.db.Base, so create_all
# needs them visible from anywhere that imports infra.db's metadata.
import persona.teacher.models  # noqa: F401

from services.knowledge.routers import (
    auth,
    documents,
    events,
    health,
    media,
    preferences,
    profile,
    retrieval,
    talk_preference,
    users,
)
from infra.event_log_middleware import install_event_log
from infra.topology import service_port


app = FastAPI(title="beWithMe knowledge")
install_event_log(app, service="knowledge")
app.include_router(health.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(talk_preference.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(retrieval.router, prefix="/api")


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
