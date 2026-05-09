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
  * /api/browser/session  — the headless `browser_set` toolset. One route,
                            action-dispatched. The persona's full headless
                            Playwright surface — goto, click, fill, type,
                            screenshot, evaluate, wait_for_*, observe, etc.
  * /api/browser/web_view/* — drives the visible Electron BrowserView via
                            the desktop HTTP shim. Distinct from session.

Run standalone:
    python -m services.browser
"""
from __future__ import annotations

import asyncio
import base64
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx
import trafilatura
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
from pydantic import BaseModel, HttpUrl

from infra.model.vision import describe_image as _describe_image
from infra.tools.web_fetch import fetch_readable, WebFetchError
from infra.topology import service_port, upstream_url
from services.browser.web_view import DesktopUnavailable, WebViewClient


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


# --- web_view: drives the Electron desktop's BrowserView via the HTTP shim ---
#
# These endpoints proxy to `desktop/src/web_view_shim.ts`, which the Electron
# main process starts on a random localhost port at app launch. Unlike
# /browser/render (Playwright headless inside the sidecar), web_view drives a
# real Chromium top-level context inside the user's app — first-party cookies,
# real Referer, no iframe / storage-partitioning failure modes. The persona
# uses this for live pages that fail in iframes (anti-embed, session-bound
# SPAs, video/canvas players).

class WebViewOpenBody(BaseModel):
    url: HttpUrl
    include_screenshot: bool = False


class WebViewObserveBody(BaseModel):
    include_screenshot: bool = False


class WebViewClickBody(BaseModel):
    selector: str | None = None
    x: int | None = None
    y: int | None = None


class WebViewTypeBody(BaseModel):
    text: str
    selector: str | None = None


class WebViewScrollBody(BaseModel):
    direction: str = "down"
    amount: int = 400


class WebViewWaitForBody(BaseModel):
    selector: str
    timeout_ms: int = 5000


def _web_view_client(request: Request) -> WebViewClient:
    return request.app.state.web_view


@router.post("/browser/web_view/open")
async def web_view_open(body: WebViewOpenBody, request: Request):
    return await _web_view_client(request).open(str(body.url), body.include_screenshot)


@router.post("/browser/web_view/observe")
async def web_view_observe(body: WebViewObserveBody, request: Request):
    return await _web_view_client(request).observe(body.include_screenshot)


@router.post("/browser/web_view/click")
async def web_view_click(body: WebViewClickBody, request: Request):
    return await _web_view_client(request).click(body.selector, body.x, body.y)


@router.post("/browser/web_view/type")
async def web_view_type(body: WebViewTypeBody, request: Request):
    return await _web_view_client(request).type(body.text, body.selector)


@router.post("/browser/web_view/scroll")
async def web_view_scroll(body: WebViewScrollBody, request: Request):
    return await _web_view_client(request).scroll(body.direction, body.amount)


@router.post("/browser/web_view/wait_for")
async def web_view_wait_for(body: WebViewWaitForBody, request: Request):
    return await _web_view_client(request).wait_for(body.selector, body.timeout_ms)


@router.post("/browser/web_view/close")
async def web_view_close(request: Request):
    return await _web_view_client(request).close()


# --- browser_set: headless Playwright session for the persona ---
#
# One global session per process. The persona drives a long-lived
# headless page via a single action-dispatched route. Action names
# match Playwright's Page API verbatim (goto, click, fill, type,
# press, screenshot, evaluate, wait_for_selector, wait_for_load_state,
# wait_for_timeout, reload, go_back, go_forward, content, title, url,
# close) plus two custom verbs:
#   - observe: drain captured XHR responses since last observe + re-read state
#   - screenshot_describe: screenshot piped through the vision model;
#     returns a textual description (raw bytes never reach the persona)


class SessionRequest(BaseModel):
    """Single request shape for /browser/session — the persona's `browser_set`
    tool. Most fields are optional; different actions consume different
    subsets.
    """

    action: str
    # Args for various actions — names match Playwright's Page API.
    url: Optional[str] = None
    selector: Optional[str] = None
    value: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    expression: Optional[str] = None
    state: Optional[str] = None
    wait_until: Optional[str] = None
    timeout: Optional[int] = None
    delay: Optional[int] = None
    full_page: bool = False
    drain: bool = True
    x: Optional[int] = None
    y: Optional[int] = None


def _session_text_response_filter(content_type: str) -> bool:
    """Generous content-type filter for response capture. Skip true binary
    only — anything text-readable (json, html, plain text, js, xml, css)
    is captured."""
    ct = (content_type or "").lower()
    if any(t in ct for t in (
        "image/", "audio/", "video/", "font/",
        "application/octet-stream", "application/pdf",
        "application/zip", "application/wasm",
    )):
        return False
    return True


async def _session_capture_response(buf: list[dict[str, Any]], target_url: str, response):
    """Best-effort response capture into the shared buffer. Fired as a
    detached task by Playwright's response listener so slow .text() reads
    don't block other event handling."""
    try:
        headers = response.headers if response.headers else {}
        ct = (headers.get("content-type", "") or "")
        if not _session_text_response_filter(ct):
            return
        if response.url == target_url:
            return  # main document HTML is captured separately
        try:
            body_text = await response.text()
        except Exception:
            return
        if not body_text:
            return
        buf.append({
            "url": response.url,
            "status": response.status,
            "content_type": ct,
            "body": body_text[:6000],
            "truncated": len(body_text) > 6000,
        })
    except Exception:
        pass


def _require_session(app: FastAPI):
    page = getattr(app.state, "session_page", None)
    if page is None or page.is_closed():
        raise HTTPException(
            status_code=400,
            detail="no active session — call goto first",
        )
    return page


async def _close_session(app: FastAPI) -> None:
    page = getattr(app.state, "session_page", None)
    if page is not None and not page.is_closed():
        try:
            await page.close()
        except Exception:
            pass
    app.state.session_page = None
    app.state.session_url = None
    app.state.session_responses = []


def _drain_responses(app: FastAPI, drain: bool) -> list[dict[str, Any]]:
    buf: list = getattr(app.state, "session_responses", []) or []
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in buf:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        deduped.append(r)
    if drain:
        buf.clear()
    return deduped[:25]


async def _read_state(app: FastAPI, drain: bool, *, page=None) -> dict[str, Any]:
    if page is None:
        page = _require_session(app)
    try:
        title = (await page.title() or "").strip()
    except Exception:
        title = ""
    try:
        html = await page.content()
    except Exception:
        html = ""
    text = ""
    try:
        text = trafilatura.extract(
            html, include_comments=False, include_tables=True, output_format="markdown",
        ) or ""
        if not text.strip():
            inner = await page.inner_text("body")
            text = "\n\n".join(line for line in (inner or "").splitlines() if line.strip())
    except Exception:
        text = ""
    text = text.strip()
    return {
        "url": getattr(app.state, "session_url", None) or page.url,
        "title": title,
        "text": text[:12000],
        "html": html[:30000],
        "responses": _drain_responses(app, drain),
        "responses_drained": drain,
    }


# ---- per-action handlers --------------------------------------------------


async def _do_goto(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    if not body.url:
        raise HTTPException(status_code=400, detail="url required")
    context = getattr(app.state, "browser_context", None)
    if context is None:
        raise HTTPException(status_code=503, detail="Browser not ready")
    await _close_session(app)

    target = body.url
    page = await context.new_page()
    app.state.session_page = page
    app.state.session_url = target
    responses: list[dict[str, Any]] = []
    app.state.session_responses = responses

    page.on(
        "response",
        lambda r: asyncio.create_task(_session_capture_response(responses, target, r)),
    )

    wait_until = body.wait_until or "domcontentloaded"
    timeout_ms = body.timeout or 20000
    try:
        response = await page.goto(target, wait_until=wait_until, timeout=timeout_ms)
    except Exception as e:
        await _close_session(app)
        raise HTTPException(status_code=400, detail=f"Failed to load URL: {e}")
    if response is None:
        await _close_session(app)
        raise HTTPException(status_code=400, detail="Failed to load URL (no response).")
    if not response.ok:
        await _close_session(app)
        raise HTTPException(status_code=400, detail=f"Failed to load URL (HTTP {response.status}).")
    # Settle: ~3s buffer so async XHRs land in the buffer before the
    # persona's first observe.
    try:
        await page.wait_for_timeout(3000)
    except Exception:
        pass
    return await _read_state(app, drain=False, page=page)


async def _do_observe(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    return await _read_state(app, drain=body.drain)


async def _do_click(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    timeout = body.timeout or 30000
    if body.selector:
        await page.click(body.selector, timeout=timeout)
    elif body.x is not None and body.y is not None:
        await page.mouse.click(body.x, body.y)
    else:
        raise HTTPException(status_code=400, detail="click needs selector or {x, y}")
    return {"ok": True}


async def _do_fill(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.selector:
        raise HTTPException(status_code=400, detail="fill needs selector")
    await page.fill(body.selector, body.value or "", timeout=body.timeout or 30000)
    return {"ok": True}


async def _do_type(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.selector:
        raise HTTPException(status_code=400, detail="type needs selector")
    if body.text is None:
        raise HTTPException(status_code=400, detail="type needs text")
    await page.type(
        body.selector, body.text,
        delay=body.delay or 0,
        timeout=body.timeout or 30000,
    )
    return {"ok": True}


async def _do_press(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.selector or not body.key:
        raise HTTPException(status_code=400, detail="press needs selector and key")
    await page.press(body.selector, body.key, timeout=body.timeout or 30000)
    return {"ok": True}


async def _do_screenshot(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    img_bytes = await page.screenshot(full_page=body.full_page)
    return {"screenshot_b64": base64.b64encode(img_bytes).decode("ascii")}


async def _do_screenshot_describe(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    img_bytes = await page.screenshot(full_page=body.full_page)
    data_url = "data:image/png;base64," + base64.b64encode(img_bytes).decode("ascii")
    try:
        description = await _describe_image(
            data_url,
            "Describe what is shown on this page in one or two short sentences. "
            "Mention any video / canvas content, error banners, loaders, or notable text.",
        )
    except Exception as e:
        return {"description": None, "error": f"vision call failed: {e}"}
    return {"description": description}


async def _do_evaluate(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.expression:
        raise HTTPException(status_code=400, detail="evaluate needs expression")
    try:
        value = await page.evaluate(body.expression)
    except Exception as e:
        return {"error": f"evaluate failed: {e}"}
    return {"value": value}


async def _do_wait_for_selector(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.selector:
        raise HTTPException(status_code=400, detail="wait_for_selector needs selector")
    try:
        await page.wait_for_selector(body.selector, timeout=body.timeout or 5000)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


async def _do_wait_for_load_state(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    state = body.state or "load"
    if state not in ("load", "domcontentloaded", "networkidle"):
        raise HTTPException(status_code=400, detail="state must be load|domcontentloaded|networkidle")
    try:
        await page.wait_for_load_state(state, timeout=body.timeout or 30000)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


async def _do_wait_for_timeout(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.timeout:
        raise HTTPException(status_code=400, detail="wait_for_timeout needs timeout (ms)")
    await page.wait_for_timeout(body.timeout)
    return {"ok": True}


async def _do_reload(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    await page.reload(wait_until=body.wait_until or "domcontentloaded")
    return {"ok": True}


async def _do_go_back(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    await page.go_back(wait_until=body.wait_until or "domcontentloaded")
    return {"ok": True}


async def _do_go_forward(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    await page.go_forward(wait_until=body.wait_until or "domcontentloaded")
    return {"ok": True}


async def _do_content(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    return {"html": await page.content()}


async def _do_title(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    return {"title": await page.title()}


async def _do_url(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    return {"url": page.url}


async def _do_close(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    await _close_session(app)
    return {"ok": True}


_SESSION_HANDLERS = {
    "goto": _do_goto,
    "observe": _do_observe,
    "click": _do_click,
    "fill": _do_fill,
    "type": _do_type,
    "press": _do_press,
    "screenshot": _do_screenshot,
    "screenshot_describe": _do_screenshot_describe,
    "evaluate": _do_evaluate,
    "wait_for_selector": _do_wait_for_selector,
    "wait_for_load_state": _do_wait_for_load_state,
    "wait_for_timeout": _do_wait_for_timeout,
    "reload": _do_reload,
    "go_back": _do_go_back,
    "go_forward": _do_go_forward,
    "content": _do_content,
    "title": _do_title,
    "url": _do_url,
    "close": _do_close,
}


@router.post("/browser/session")
async def browser_session(body: SessionRequest, request: Request):
    handler = _SESSION_HANDLERS.get((body.action or "").lower())
    if handler is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown action: {body.action!r}. valid: {sorted(_SESSION_HANDLERS)}",
        )
    return await handler(request.app, body)


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
    try:
        yield
    finally:
        await _close_session(app)
        await app.state.http.aclose()
        await context.close()
        await pw.stop()


app = FastAPI(title="beWithMe browser", lifespan=lifespan)
app.include_router(router, prefix="/api")


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
