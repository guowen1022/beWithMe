"""web_view routes — drive the Electron desktop's BrowserView via the HTTP shim.

These endpoints proxy to `desktop/src/web_view_shim.ts`, which the Electron main
process starts on a random localhost port at app launch. Unlike `/browser/render`
(Playwright headless inside the sidecar), web_view drives a real Chromium
top-level context inside the user's app — first-party cookies, real Referer, no
iframe / storage-partitioning failure modes. The persona uses this for live
pages that fail in iframes (anti-embed, session-bound SPAs, video/canvas
players). The `WebViewClient` itself lives in `services/browser/web_view.py`;
this module is just the route surface. Extracted verbatim from `main.py` (F6).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, HttpUrl

from services.browser.web_view import WebViewClient

router = APIRouter()


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
