from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import health, profile, ask, interactions, documents, preferences, concepts

app = FastAPI(title="beWithMe", description="Personalized Reading Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(ask.router, prefix="/api")
app.include_router(interactions.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(concepts.router, prefix="/api")
