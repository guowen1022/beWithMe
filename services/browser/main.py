"""Browser sidecar — :BASE_PORT+5.

Owns the Playwright BrowserContext and all page-lifecycle state, and wires up
the route surface. The endpoints live in three modules (F6 split):

  * browse.py           — /browser/{status,selection,handoff,render,resume}
                          (the visible page + the knowledge-sidecar render hook)
  * web_view_routes.py  — /browser/web_view/* (drives the Electron BrowserView
                          via the desktop HTTP shim)
  * session.py          — /browser/session (the headless `browser_set` toolset,
                          action-dispatched), backed by snapshot.py (@ref layer)

All shared Playwright state lives on `app.state`, set up in `lifespan` below.

Run standalone:
    python -m services.browser
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright

from infra.event_log_middleware import install_event_log
from infra.topology import service_port
from services.browser.web_view import DesktopUnavailable, WebViewClient
from services.browser.browse import router as browse_router
from services.browser.web_view_routes import router as web_view_router
from services.browser.session import router as session_router, close_session


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
    app.state.browse_page = None
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=False)
    app.state.web_view = WebViewClient()
    # browser_set session state
    app.state.session_page = None
    app.state.session_url = None
    app.state.session_responses = []
    # @ref → Locator map, populated by action='snapshot', invalidated on
    # any navigation. Empty until first snapshot.
    app.state.session_refs = {}
    try:
        yield
    finally:
        await close_session(app)
        await app.state.http.aclose()
        await context.close()
        await pw.stop()


app = FastAPI(title="beWithMe browser", lifespan=lifespan)
install_event_log(app, service="browser")
app.include_router(browse_router, prefix="/api")
app.include_router(web_view_router, prefix="/api")
app.include_router(session_router, prefix="/api")


# Map DesktopUnavailable to 503 with a stable shape the persona-side tool
# can recognise as "Electron isn't running" (vs. a real failure). Keeps the
# route handlers' signatures clean for FastAPI introspection.
@app.exception_handler(DesktopUnavailable)
async def _desktop_unavailable_handler(request: Request, exc: DesktopUnavailable):
    return JSONResponse(
        status_code=503,
        content={"error": "desktop_not_running", "detail": str(exc)},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.browser.main:app",
        host="0.0.0.0",
        port=service_port("browser"),
        reload=False,
    )


if __name__ == "__main__":
    main()
