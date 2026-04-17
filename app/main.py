import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from app.api import health, users, profile, ask, interactions, documents, preferences, concepts, sessions, browser

BROWSER_PROFILE_DIR = Path("data/browser_profile")


@asynccontextmanager
async def lifespan(app: FastAPI):
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    headed = os.getenv("BROWSER_HEADED") == "1"
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_PROFILE_DIR),
        headless=not headed,
        viewport={"width": 1280, "height": 720},
    )
    app.state.playwright = pw
    app.state.browser_context = context
    app.state.browser_headed = headed
    app.state.handoff_page = None
    try:
        yield
    finally:
        await context.close()
        await pw.stop()


app = FastAPI(title="beWithMe", description="Personalized Reading Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(ask.router, prefix="/api")
app.include_router(interactions.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(concepts.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(browser.router, prefix="/api")
