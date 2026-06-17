"""Headless Playwright session — the persona's `browser_set` toolset.

One global session per process: a long-lived headless page the persona drives
via a single action-dispatched route (`POST /browser/session`). Action names
match Playwright's Page API (goto, click, fill, type, press, screenshot,
evaluate, wait_for_*, reload, go_back, go_forward, content, title, url, close)
plus two custom verbs:
  - observe: drain captured XHR responses since last observe + re-read state
  - screenshot_describe: screenshot piped through the vision model

All page state lives on `app.state` (session_page/url/responses/refs), set up by
`main.py:lifespan`. The @ref accessibility layer lives in `snapshot.py`.
Extracted verbatim from `main.py` (F6).
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel
import trafilatura

from infra.model.vision import describe_image as _describe_image
from services.browser.snapshot import (
    DEFAULT_MAIN_SCOPES,
    MAX_NAME_LEN,
    MAX_REFS,
    SECTION_TEXT_JS,
    invalidate_refs,
    level_from_attrs,
    parse_aria_snapshot,
    resolve_locator,
)

router = APIRouter()


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


async def close_session(app: FastAPI) -> None:
    page = getattr(app.state, "session_page", None)
    if page is not None and not page.is_closed():
        try:
            await page.close()
        except Exception:
            pass
    app.state.session_page = None
    app.state.session_url = None
    app.state.session_responses = []
    # Invalidate snapshot refs on session teardown.
    app.state.session_refs = {}


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
    await close_session(app)
    invalidate_refs(app)

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
        await close_session(app)
        raise HTTPException(status_code=400, detail=f"Failed to load URL: {e}")
    if response is None:
        await close_session(app)
        raise HTTPException(status_code=400, detail="Failed to load URL (no response).")
    if not response.ok:
        await close_session(app)
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
    loc = await resolve_locator(app, page, body.selector)
    if loc is not None:
        await loc.click(timeout=timeout)
    elif body.selector:
        await page.click(body.selector, timeout=timeout)
    elif body.x is not None and body.y is not None:
        await page.mouse.click(body.x, body.y)
    else:
        raise HTTPException(status_code=400, detail="click needs selector, @ref, or {x, y}")
    return {"ok": True}


async def _do_fill(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.selector:
        raise HTTPException(status_code=400, detail="fill needs selector")
    timeout = body.timeout or 30000
    loc = await resolve_locator(app, page, body.selector)
    if loc is not None:
        await loc.fill(body.value or "", timeout=timeout)
    else:
        await page.fill(body.selector, body.value or "", timeout=timeout)
    return {"ok": True}


async def _do_type(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.selector:
        raise HTTPException(status_code=400, detail="type needs selector")
    if body.text is None:
        raise HTTPException(status_code=400, detail="type needs text")
    timeout = body.timeout or 30000
    loc = await resolve_locator(app, page, body.selector)
    if loc is not None:
        await loc.type(body.text, delay=body.delay or 0, timeout=timeout)
    else:
        await page.type(body.selector, body.text, delay=body.delay or 0, timeout=timeout)
    return {"ok": True}


async def _do_press(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    if not body.selector or not body.key:
        raise HTTPException(status_code=400, detail="press needs selector and key")
    timeout = body.timeout or 30000
    loc = await resolve_locator(app, page, body.selector)
    if loc is not None:
        await loc.press(body.key, timeout=timeout)
    else:
        await page.press(body.selector, body.key, timeout=timeout)
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
    invalidate_refs(app)
    return {"ok": True}


async def _do_go_back(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    await page.go_back(wait_until=body.wait_until or "domcontentloaded")
    invalidate_refs(app)
    return {"ok": True}


async def _do_go_forward(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    page = _require_session(app)
    await page.go_forward(wait_until=body.wait_until or "domcontentloaded")
    invalidate_refs(app)
    return {"ok": True}


async def _do_snapshot(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    """Capture an ARIA snapshot of the page, assign @e refs to interesting
    nodes, and return a compact YAML-ish tree the LLM can scan. Refs
    persist on app.state until the next navigation.

    Scope resolution: if `selector` is set, snapshot that subtree. Otherwise
    auto-detect a main-content scope (`main`, `article`, `#bodyContent`,
    ...) to skip page chrome — falls back to `body` if none match. The
    auto-detected scope is reported back as `scope_used`.
    """
    page = _require_session(app)

    target = None
    scope_used = None
    if body.selector:
        target = page.locator(body.selector)
        scope_used = body.selector
    else:
        for sel in DEFAULT_MAIN_SCOPES:
            try:
                count = await page.locator(sel).count()
            except Exception:
                continue
            if count > 0:
                target = page.locator(sel).first
                scope_used = sel
                break
    if target is None:
        target = page.locator("body")
        scope_used = "body"

    try:
        yaml_text = await target.aria_snapshot(timeout=body.timeout or 5000)
    except Exception as e:
        return {"error": f"snapshot failed: {e}", "refs": [], "tree": "", "scope_used": scope_used}
    if not yaml_text:
        return {"error": "empty snapshot", "refs": [], "tree": "", "scope_used": scope_used}

    refs_map, tree_text = parse_aria_snapshot(yaml_text, page)
    app.state.session_refs = refs_map

    # Headings get their own flat list — the tree text is capped at
    # _MAX_TREE_CHARS and on a long article (Wikipedia: ~30 H2s + many
    # H3s) the back-half of headings can fall off the displayed tree.
    # The flat list ensures all headings are addressable even when the
    # tree display truncates.
    headings_flat = [
        {"ref": r, "level": level_from_attrs(v.get("attrs", "")), "name": v["name"][:MAX_NAME_LEN]}
        for r, v in refs_map.items() if v["role"] == "heading"
    ]

    # The non-heading interactive refs are more numerous and less
    # central — leave them to the tree display.
    ref_summary = [
        {
            "ref": r,
            "role": v["role"],
            "name": v["name"][:MAX_NAME_LEN],
        }
        for r, v in refs_map.items()
    ]

    return {
        "ref_count": len(refs_map),
        "heading_count": len(headings_flat),
        "headings": headings_flat,
        "tree": tree_text,
        "refs": ref_summary,
        "scope_used": scope_used,
        "truncated": len(refs_map) >= MAX_REFS,
        "note": (
            f"{len(refs_map)} refs ({len(headings_flat)} headings), scoped "
            f"to {scope_used!r}. The flat `headings` field lists every "
            "addressable heading even when the tree display truncates. "
            "Use @e<n> in click/text/scroll/fill/type. For HEADINGS, "
            "text @e<heading> returns the whole section (heading + content "
            "until next same-or-higher heading). Refs invalidate on "
            "goto/reload/back/forward."
        ),
    }


async def _do_text(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    """Return the visible text of a specific element. Takes either @ref
    (preferred) or a CSS selector. Use this after `snapshot` to read just
    one section instead of dragging the whole page text back to the LLM.

    Heading-aware: if the target's role is `heading`, returns the whole
    section (heading + every following sibling until the next same-or-
    higher-level heading). That's what "give me the Criticisms section"
    actually means.
    """
    page = _require_session(app)
    if not body.selector:
        raise HTTPException(status_code=400, detail="text needs selector or @ref")
    timeout = body.timeout or 5000

    # Check if the @ref points at a heading — if so, use section-text JS.
    is_heading = False
    refs = getattr(app.state, "session_refs", None) or {}
    if body.selector.startswith("@e"):
        entry = refs.get(body.selector)
        if entry and entry.get("role") == "heading":
            is_heading = True

    loc = await resolve_locator(app, page, body.selector)
    try:
        if is_heading and loc is not None:
            inner = await loc.evaluate(SECTION_TEXT_JS, timeout=timeout)
        elif loc is not None:
            inner = await loc.inner_text(timeout=timeout)
        else:
            inner = await page.inner_text(body.selector, timeout=timeout)
    except Exception as e:
        return {"error": f"text failed: {e}"}
    text = (inner or "").strip()
    return {
        "text": text[:8000],
        "length": len(text),
        "truncated": len(text) > 8000,
        "section_text": is_heading,
    }


async def _do_scroll(app: FastAPI, body: SessionRequest) -> dict[str, Any]:
    """Scroll an element (by @ref or CSS selector) into view. After this,
    a screenshot or observe will reflect the new viewport."""
    page = _require_session(app)
    if not body.selector:
        raise HTTPException(status_code=400, detail="scroll needs selector or @ref")
    timeout = body.timeout or 5000
    loc = await resolve_locator(app, page, body.selector)
    try:
        if loc is not None:
            await loc.scroll_into_view_if_needed(timeout=timeout)
        else:
            await page.locator(body.selector).first.scroll_into_view_if_needed(timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": str(e)}
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
    await close_session(app)
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
    "snapshot": _do_snapshot,
    "text": _do_text,
    "scroll": _do_scroll,
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
