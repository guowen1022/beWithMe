"""Browser sidecar — :BASE_PORT+5.

Owns the Playwright BrowserContext and all page-lifecycle state. Exposes:

  * /api/browser/status   — public, used by the frontend
  * /api/browser/selection — public, returns user selection text
  * /api/browser/handoff  — public, opens a URL for manual interaction
  * /api/browser/resume   — public, extracts text from active handoff page
                            and POSTs it to the knowledge sidecar to persist
  * /api/browser/render   — internal, used by knowledge's /documents/url:
                            render a URL and return {title, text}; keeps page
                            open if BROWSER_HEADED=1 so user can browse

Run standalone:
    python -m services.browser
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import trafilatura
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
from pydantic import BaseModel, HttpUrl

from app.infra.tools.web_fetch import fetch_readable, WebFetchError
from services.shell.proxy import service_port, upstream_url


BROWSER_PROFILE_DIR = Path("data/browser_profile")

router = APIRouter()


class HandoffRequest(BaseModel):
    url: HttpUrl


class RenderRequest(BaseModel):
    url: HttpUrl


def _active_page(app: FastAPI):
    """Return the page the user is currently viewing (browse or handoff)."""
    for attr in ("browse_page", "handoff_page"):
        page = getattr(app.state, attr, None)
        if page and not page.is_closed():
            return page
    return None


@router.get("/browser/status")
async def browser_status(request: Request):
    context = getattr(request.app.state, "browser_context", None)
    headed = getattr(request.app.state, "browser_headed", False)
    if context is None:
        return {"status": "not_running", "headed": False}
    return {
        "status": "running",
        "headed": headed,
        "pages": len(context.pages),
        "urls": [p.url for p in context.pages],
    }


@router.get("/browser/selection")
async def browser_selection(request: Request):
    """Return the text the user currently has selected in the browser page."""
    page = _active_page(request.app)
    if not page:
        return {"selection": "", "url": ""}
    try:
        sel = await page.evaluate("window.getSelection().toString()")
        return {"selection": (sel or "").strip(), "url": page.url}
    except Exception:
        return {"selection": "", "url": ""}


@router.post("/browser/handoff")
async def browser_handoff(body: HandoffRequest, request: Request):
    """Open a URL in the visible browser window for manual interaction (captcha,
    login, cookie consent). Call /browser/resume when done to extract content."""
    context = getattr(request.app.state, "browser_context", None)
    headed = getattr(request.app.state, "browser_headed", False)
    if context is None:
        raise HTTPException(status_code=503, detail="Browser not ready")
    if not headed:
        raise HTTPException(
            status_code=400,
            detail="Browser is headless. Restart with BROWSER_HEADED=1 to enable handoff.",
        )

    old_page = getattr(request.app.state, "handoff_page", None)
    if old_page and not old_page.is_closed():
        await old_page.close()

    page = await context.new_page()
    try:
        await page.goto(str(body.url), wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        await page.close()
        raise HTTPException(status_code=400, detail=f"Failed to load URL: {e}")
    await page.bring_to_front()
    request.app.state.handoff_page = page
    return {
        "status": "ok",
        "message": "Browser opened. Solve the captcha or log in, then click Resume.",
    }


@router.post("/browser/render")
async def browser_render(body: RenderRequest, request: Request):
    """Internal endpoint: render a URL via the shared Chromium context and
    return the extracted readable text. Called by the knowledge sidecar to
    fulfill /documents/url. If the browser is headed, keeps the page open and
    brings it to front so the user can browse / select.
    """
    context = getattr(request.app.state, "browser_context", None)
    if context is None:
        raise HTTPException(status_code=503, detail="Browser not ready")

    headed = getattr(request.app.state, "browser_headed", False)
    try:
        title, text, page = await fetch_readable(str(body.url), context, keep_open=headed)
    except WebFetchError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if page:
        old = getattr(request.app.state, "browse_page", None)
        if old and not old.is_closed():
            await old.close()
        request.app.state.browse_page = page
        await page.bring_to_front()

    return {"title": title, "text": text, "url": str(body.url)}


@router.post("/browser/resume")
async def browser_resume(request: Request):
    """Extract text from the handoff page after the user has interacted with it
    (solved captcha, logged in, etc.). Forwards to the knowledge sidecar to
    create the document + trigger embedding."""
    page = getattr(request.app.state, "handoff_page", None)
    if page is None or page.is_closed():
        raise HTTPException(status_code=400, detail="No active handoff. Call /browser/handoff first.")

    try:
        html = await page.content()
        title = ((await page.title()) or page.url).strip()

        text = trafilatura.extract(
            html, include_comments=False, include_tables=True, output_format="markdown",
        )
        if not text or not text.strip():
            try:
                text = await page.inner_text("body")
                if text:
                    text = "\n\n".join(line for line in text.splitlines() if line.strip())
            except Exception:
                text = None

        if not text or not text.strip():
            raise HTTPException(status_code=400, detail="Could not extract readable text from this page.")

        text = text.strip()
    finally:
        await page.close()
        request.app.state.handoff_page = None

    # Forward to knowledge sidecar to persist + trigger embedding. Pass through
    # the original X-User-Id header from the caller.
    user_header = request.headers.get("x-user-id")
    if not user_header:
        raise HTTPException(status_code=401, detail="missing X-User-Id header")

    client: httpx.AsyncClient = request.app.state.http
    try:
        upstream = await client.post(
            f"{upstream_url('knowledge')}/api/documents/from-extracted",
            json={"title": title, "text": text, "filename": None, "url": page.url if not page.is_closed() else None},
            headers={"X-User-Id": user_header},
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"knowledge sidecar unreachable: {e}")

    return JSONResponse(content=upstream.json(), status_code=upstream.status_code)


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
    try:
        yield
    finally:
        await app.state.http.aclose()
        await context.close()
        await pw.stop()


app = FastAPI(title="beWithMe browser", lifespan=lifespan)
app.include_router(router, prefix="/api")


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
