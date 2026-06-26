"""web_view — drive the desktop's real Chromium pane.

Unlike `request_new_block` (which generates an iframe-bearing block, and
fails on sites with anti-embedding / first-party-cookie SPAs), web_view
loads the URL into the Electron BrowserView. That's a top-level Chromium
context with its own persistent profile — first-party cookies, real
Referer, no `window.top` self-checks, no iframe storage partitioning.

The tool itself never touches the BrowserView directly. It HTTP-POSTs
to the browser sidecar (port BASE_PORT+5), which proxies to the Electron
HTTP shim (`desktop/src/web_view_shim.ts`).

Returns a structured perception report — never raw image bytes. When
`include_screenshot=True`, the sidecar runs the screenshot through the
vision facade (`infra.model.vision.describe_image`, Doubao by default)
and folds the description in as `screenshot_description`. The text-only
DeepSeek persona reasons over text only.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

import httpx

from infra.contracts.ui import BlockSource, UIUpdate
from infra.topology import upstream_url
from infra.devices.delivery import enqueue_for_user
from workshop.canvas.tools.mount_template import mount_template
from infra.model.tools import ToolSpec


_TIMEOUT = httpx.Timeout(60.0)
_BLOCK_ID = "web-view"  # kebab form of the template name; matches mount_template's id_default


async def _post(action: str, payload: dict[str, Any]) -> dict:
    url = f"{upstream_url('browser').rstrip('/')}/api/browser/web_view/{action}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, json=payload)
        except httpx.HTTPError as e:
            return {"error": f"browser sidecar unreachable: {e}"}
    try:
        data = resp.json()
    except Exception:
        return {"error": f"sidecar non-JSON response (HTTP {resp.status_code})"}
    if resp.status_code >= 400 and "error" not in data:
        data["error"] = f"sidecar HTTP {resp.status_code}"
    return data


async def web_view(
    *,
    user_id: UUID,  # noqa: ARG001 — reserved for future per-user shims
    action: str,
    url: Optional[str] = None,
    selector: Optional[str] = None,
    text: Optional[str] = None,
    direction: str = "down",
    amount: int = 400,
    timeout_ms: int = 5000,
    include_screenshot: bool = False,
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> dict:
    action = (action or "").strip().lower()
    if action == "open":
        if not url:
            return {"error": "url is required for action='open'"}
        # Mount the positional block first so the BrowserView snaps to the
        # block's grid rect (its onMount calls bridge.setBounds). If the
        # block is already mounted, mount_template re-emits the mount event
        # — the existing block instance just re-syncs bounds. Concurrently
        # load the URL into the BrowserView via the shim.
        mount_err: Optional[str] = None
        try:
            await mount_template(user_id=user_id, template_name="web_view")
        except Exception as e:
            # Mounting is a UX nicety. If it fails (template missing,
            # sandbox rejection, no canvas), the page still loads in
            # whatever bounds the BrowserView has — V1's centered default
            # is the safety net. Surface so the persona knows.
            mount_err = f"failed to mount web_view block: {e}"
        report = await _post(
            "open", {"url": url, "include_screenshot": include_screenshot}
        )
        if mount_err and "error" not in report:
            report["mount_warning"] = mount_err
        return report
    if action == "observe":
        return await _post("observe", {"include_screenshot": include_screenshot})
    if action == "click":
        body: dict[str, Any] = {}
        if selector:
            body["selector"] = selector
        elif x is not None and y is not None:
            body["x"] = x
            body["y"] = y
        else:
            return {"error": "click needs selector or {x, y}"}
        return await _post("click", body)
    if action == "type":
        if not text:
            return {"error": "text is required for action='type'"}
        body = {"text": text}
        if selector:
            body["selector"] = selector
        return await _post("type", body)
    if action == "scroll":
        if direction not in ("up", "down"):
            return {"error": "direction must be 'up' or 'down'"}
        return await _post("scroll", {"direction": direction, "amount": int(amount)})
    if action == "wait_for":
        if not selector:
            return {"error": "selector is required for action='wait_for'"}
        return await _post(
            "wait_for", {"selector": selector, "timeout_ms": int(timeout_ms)}
        )
    if action == "close":
        # Unmount the block (its cleanup hides the BrowserView via the
        # bridge) AND tell the shim to hide as a belt-and-suspenders —
        # the unmount path runs in the renderer, the shim path runs in
        # main; either alone is sufficient, both is harmless.
        try:
            await enqueue_for_user(
                user_id,
                UIUpdate(action="unmount", block=BlockSource(id=_BLOCK_ID, source="")),
            )
        except Exception:
            # SSE fan-out failure is non-fatal — the user-facing pane will
            # still hide via the shim call below.
            pass
        return await _post("close", {})
    return {"error": f"unknown action: {action!r}"}


__all__ = ["web_view", "build_spec"]

def _make_web_view(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        action = (args.get("action") or "").strip().lower()
        if not action:
            return json.dumps({"error": "action is required"})
        url = args.get("url")
        selector = args.get("selector")
        text = args.get("text")
        direction = args.get("direction") or "down"
        amount_raw = args.get("amount")
        try:
            amount = int(amount_raw) if amount_raw is not None else 400
        except (TypeError, ValueError):
            return json.dumps({"error": "amount must be an integer"})
        timeout_raw = args.get("timeout_ms")
        try:
            timeout_ms = int(timeout_raw) if timeout_raw is not None else 5000
        except (TypeError, ValueError):
            return json.dumps({"error": "timeout_ms must be an integer"})
        include_screenshot = bool(args.get("include_screenshot") or False)
        x = args.get("x")
        y = args.get("y")
        try:
            x = int(x) if x is not None else None
            y = int(y) if y is not None else None
        except (TypeError, ValueError):
            return json.dumps({"error": "x and y must be integers"})
        try:
            result = await web_view(
                user_id=user_id,
                action=action,
                url=url if isinstance(url, str) else None,
                selector=selector if isinstance(selector, str) else None,
                text=text if isinstance(text, str) else None,
                direction=direction,
                amount=amount,
                timeout_ms=timeout_ms,
                include_screenshot=include_screenshot,
                x=x,
                y=y,
            )
        except Exception as e:
            return json.dumps({"error": f"web_view call failed: {e}"})
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="web_view",
        description=(
            "Visible web pane — the desktop's real Chromium "
            "BrowserView, mounted on the canvas as a draggable / "
            "resizable / closeable block. NOT the default for shared "
            "URLs — for 'explain this article', 'what is this', "
            "'summarise this paper', use read_url instead, then speak "
            "the answer. Reach for web_view ONLY when the user "
            "explicitly wants to see / play / interact with the page: "
            "'show me', 'open it', 'play this replay', 'let me watch', "
            "or when read_url failed to extract text (canvas / video / "
            "image-only SPAs). The page loads first-party (real "
            "cookies, real Referer), so anti-embed sites, DRM "
            "players, session-bound SPAs, video/canvas replays that "
            "'just don't work' inside request_new_block work here. "
            "Available actions: "
            "'open' (loads url, returns perception), 'observe' (re-checks "
            "current page without re-navigating — call twice ~2s apart "
            "to see if a video is actually playing via "
            "video.current_time advancing), 'click' (selector OR x+y), "
            "'type' (text into a focused or selected element), 'scroll' "
            "(direction up/down + pixel amount), 'wait_for' (selector "
            "appears within timeout_ms), 'close' (hide the pane). The "
            "perception report includes title, url, ready_state, "
            "loader_visible, video state (current_time, duration, "
            "paused), canvas presence, visible text excerpt, console "
            "errors, and failed network requests. Set "
            "include_screenshot=true on open/observe ONLY when DOM "
            "probes aren't enough — the screenshot is delegated to a "
            "vision model and the textual description is added to the "
            "report as `screenshot_description` (the persona never sees "
            "raw image bytes). include_screenshot adds ~5–6s. If the "
            "desktop isn't running, returns "
            "{\"error\": \"desktop_not_running\"} so you can speak the "
            "limitation back to the user."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "open", "observe", "click", "type",
                        "scroll", "wait_for", "close",
                    ],
                },
                "url": {
                    "type": "string",
                    "description": "Required for action='open'.",
                },
                "selector": {
                    "type": "string",
                    "description": (
                        "CSS selector. For click/type, alternative to x+y. "
                        "Required for wait_for."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Text to type. Required for action='type'.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction. Default 'down'.",
                },
                "amount": {
                    "type": "integer",
                    "description": "Scroll distance in pixels. Default 400.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Timeout for wait_for. Default 5000.",
                },
                "include_screenshot": {
                    "type": "boolean",
                    "description": (
                        "If true, capture a screenshot, run it through "
                        "the vision model, and add the textual "
                        "description to the report. Adds ~5–6s."
                    ),
                },
                "x": {
                    "type": "integer",
                    "description": "Click x-coordinate (alternative to selector).",
                },
                "y": {
                    "type": "integer",
                    "description": "Click y-coordinate (alternative to selector).",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        executor=_make_web_view(user_id),
    )
