"""browser_set — comprehensive headless browser toolkit for the persona.

Single tool, action-dispatched. Action names match Playwright's Page
API verbatim (`goto`, `click`, `fill`, `type`, `press`, `screenshot`,
`evaluate`, `wait_for_selector`, `wait_for_load_state`,
`wait_for_timeout`, `reload`, `go_back`, `go_forward`, `content`,
`title`, `url`, `close`) plus two custom verbs:

  - `observe`: drain XHR responses captured since last observe + re-read
    text/html. The streaming-friendly mode.
  - `screenshot_describe`: screenshot piped through the vision model;
    returns a textual description (raw bytes never reach the persona).

All calls go to a single sidecar route `/api/browser/session`. The
sidecar holds one global headless Playwright Page; `goto` replaces any
prior session.

For the common one-shot read pattern (`goto` + `close`), use the
`read_url` convenience wrapper instead.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID  # noqa: F401 — kept for symmetry with other tools

import httpx

from infra.topology import upstream_url
from infra.model.tools import ToolSpec


# Vision + load + settle timeline can stack to ~25s on slow SPAs;
# 60s gives headroom without hanging forever.
_TIMEOUT = httpx.Timeout(60.0)
_MAX_RESPONSES = 12  # cap responses returned to the persona per call


_VALID_ACTIONS = {
    "goto", "observe", "click", "fill", "type", "press",
    "screenshot", "screenshot_describe", "evaluate",
    "wait_for_selector", "wait_for_load_state", "wait_for_timeout",
    "reload", "go_back", "go_forward",
    "content", "title", "url", "close",
    # Accessibility-snapshot + @ref selection — the cheap path for
    # "find / read a specific section" without writing JS.
    "snapshot", "text", "scroll",
}


async def browser_set(
    *,
    user_id: UUID,  # noqa: ARG001 — reserved for future per-user sessions
    action: str,
    url: Optional[str] = None,
    selector: Optional[str] = None,
    value: Optional[str] = None,
    text: Optional[str] = None,
    key: Optional[str] = None,
    expression: Optional[str] = None,
    state: Optional[str] = None,
    wait_until: Optional[str] = None,
    timeout: Optional[int] = None,
    delay: Optional[int] = None,
    full_page: bool = False,
    drain: bool = True,
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> dict:
    """Dispatch a Playwright-named action against the headless session.

    Returns the action-specific payload, or {error: "..."} on failure.
    """
    action = (action or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return {
            "error": f"unknown action: {action!r}. valid: {sorted(_VALID_ACTIONS)}"
        }

    body: dict[str, Any] = {"action": action}
    if url is not None:
        body["url"] = url
    if selector is not None:
        body["selector"] = selector
    if value is not None:
        body["value"] = value
    if text is not None:
        body["text"] = text
    if key is not None:
        body["key"] = key
    if expression is not None:
        body["expression"] = expression
    if state is not None:
        body["state"] = state
    if wait_until is not None:
        body["wait_until"] = wait_until
    if timeout is not None:
        body["timeout"] = timeout
    if delay is not None:
        body["delay"] = delay
    if full_page:
        body["full_page"] = True
    if action == "observe":
        body["drain"] = drain
    if x is not None:
        body["x"] = x
    if y is not None:
        body["y"] = y

    endpoint = f"{upstream_url('browser').rstrip('/')}/api/browser/session"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(endpoint, json=body)
        except httpx.HTTPError as e:
            return {"error": f"browser sidecar unreachable: {e}"}

    try:
        data = resp.json()
    except Exception:
        return {"error": f"sidecar non-JSON response (HTTP {resp.status_code})"}

    if resp.status_code >= 400:
        return {"error": data.get("detail") or f"sidecar HTTP {resp.status_code}"}

    # Bound `responses` so a chatty page doesn't blow up the persona's
    # context. Sidecar already de-dupes and caps at 25; trim further here.
    if isinstance(data.get("responses"), list) and len(data["responses"]) > _MAX_RESPONSES:
        data["responses"] = data["responses"][:_MAX_RESPONSES]

    return data


__all__ = ["browser_set", "build_spec"]

def _make_browser_set(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        action = (args.get("action") or "").strip().lower()
        if not action:
            return json.dumps({"error": "action is required"})
        # Numeric coercion for fields that may arrive as strings
        timeout = args.get("timeout")
        delay = args.get("delay")
        x = args.get("x")
        y = args.get("y")
        try:
            timeout = int(timeout) if timeout is not None else None
            delay = int(delay) if delay is not None else None
            x = int(x) if x is not None else None
            y = int(y) if y is not None else None
        except (TypeError, ValueError):
            return json.dumps({"error": "timeout/delay/x/y must be integers"})

        try:
            result = await browser_set(
                user_id=user_id,
                action=action,
                url=args.get("url") if isinstance(args.get("url"), str) else None,
                selector=args.get("selector") if isinstance(args.get("selector"), str) else None,
                value=args.get("value") if isinstance(args.get("value"), str) else None,
                text=args.get("text") if isinstance(args.get("text"), str) else None,
                key=args.get("key") if isinstance(args.get("key"), str) else None,
                expression=args.get("expression") if isinstance(args.get("expression"), str) else None,
                state=args.get("state") if isinstance(args.get("state"), str) else None,
                wait_until=args.get("wait_until") if isinstance(args.get("wait_until"), str) else None,
                timeout=timeout,
                delay=delay,
                full_page=bool(args.get("full_page") or False),
                drain=bool(args.get("drain", True)),
                x=x,
                y=y,
            )
        except Exception as e:
            return json.dumps({"error": f"browser_set failed: {e}"})
        # Snapshot results carry a ref→locator map that doesn't serialize;
        # strip the locator-internal field so we send a clean payload back.
        # (the sidecar already does this, but be defensive.)
        if isinstance(result, dict) and isinstance(result.get("refs"), list):
            for r in result["refs"]:
                if isinstance(r, dict):
                    r.pop("locator", None)
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="browser_set",
        description=(
            "Comprehensive headless browser toolkit. Use whenever you "
            "need to read, interact with, or capture state from a "
            "web page WITHOUT showing it to the user. The page runs "
            "in a long-lived headless Chromium tab on the sidecar; "
            "one global session per process. Action names match "
            "Playwright's Page API verbatim — if you know "
            "Playwright, you already know how to use this:\n"
            "  - goto(url, wait_until?): page.goto. Loads URL, waits, "
            "captures XHRs. Returns {url, title, text, html, responses}.\n"
            "  - observe(drain=true): drain XHR responses captured "
            "since last observe + re-read text/html. Use after "
            "click/fill/wait, or while polling a live feed.\n"
            "  - SNAPSHOT / @REF (the cheap path for 'find a section'): "
            "snapshot() walks the ARIA tree and returns a compact list "
            "of @e1, @e2, ... refs for every heading / link / button / "
            "section / paragraph. Then call text(selector='@e42') to "
            "read just that section, or click(selector='@e7') to click "
            "it, or scroll(selector='@e42') to bring it into view. "
            "PREFER snapshot+@ref over evaluate for navigating long "
            "pages — it's faster, doesn't require writing JS, and "
            "doesn't blow up your context. Refs invalidate on any "
            "goto/reload/back/forward; re-snapshot after navigation.\n"
            "  - text(selector | @ref): return the inner text of one "
            "element. Use after snapshot to read a specific section "
            "instead of dragging the whole page through read_url.\n"
            "  - scroll(selector | @ref): scroll an element into view.\n"
            "  - click(selector | @ref | x,y, timeout?).\n"
            "  - fill(selector | @ref, value): set input value.\n"
            "  - type(selector | @ref, text, delay?): keystrokes.\n"
            "  - press(selector | @ref, key): e.g. key='Enter'.\n"
            "  - screenshot(full_page?): returns base64 PNG.\n"
            "  - screenshot_describe(full_page?): screenshot piped "
            "through the vision model; returns a textual description "
            "(you never see raw bytes). Costs ~5–6s.\n"
            "  - evaluate(expression): page.evaluate — JS, returns "
            "JSON-serialised result. RESERVED for reading window "
            "globals (window.__INITIAL_STATE__) or computed values "
            "that snapshot can't expose. DO NOT use evaluate to grep "
            "page text or scroll to anchors — snapshot + text/scroll "
            "do that better.\n"
            "  - wait_for_selector(selector | @ref, timeout?), "
            "wait_for_load_state(state), wait_for_timeout(timeout).\n"
            "  - reload, go_back, go_forward (all invalidate @refs).\n"
            "  - content (HTML), title, url.\n"
            "  - close(): close the page when done.\n"
            "Default flow for 'read this URL' is just read_url (a "
            "shortcut for goto+close). For 'find a specific section' "
            "on an already-loaded page: snapshot → text @eN. For "
            "interactive flows: goto → snapshot → click @eN → "
            "snapshot (refs invalidated by nav) → observe → close. "
            "Use web_view(open) instead ONLY when the user explicitly "
            "asks to SEE the page (replays, login walls, manual "
            "interaction)."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "goto", "observe", "click", "fill", "type",
                        "press", "screenshot", "screenshot_describe",
                        "evaluate", "wait_for_selector",
                        "wait_for_load_state", "wait_for_timeout",
                        "reload", "go_back", "go_forward",
                        "content", "title", "url", "close",
                        "snapshot", "text", "scroll",
                    ],
                },
                "url": {"type": "string", "description": "For goto."},
                "selector": {
                    "type": "string",
                    "description": (
                        "CSS selector OR @e<n> ref from a prior "
                        "snapshot. For click/fill/type/press/"
                        "wait_for_selector/text/scroll."
                    ),
                },
                "value": {"type": "string", "description": "For fill."},
                "text": {"type": "string", "description": "For type."},
                "key": {"type": "string", "description": "For press, e.g. 'Enter'."},
                "expression": {"type": "string", "description": "JS expression for evaluate."},
                "state": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "description": "For wait_for_load_state.",
                },
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "description": "For goto / reload / go_back / go_forward.",
                },
                "timeout": {"type": "integer", "description": "Milliseconds."},
                "delay": {"type": "integer", "description": "ms between keystrokes for type."},
                "full_page": {
                    "type": "boolean",
                    "description": "For screenshot / screenshot_describe.",
                },
                "drain": {
                    "type": "boolean",
                    "description": "For observe; if false, keep responses in buffer.",
                },
                "x": {"type": "integer", "description": "Click x-coord (alternative to selector)."},
                "y": {"type": "integer", "description": "Click y-coord."},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        executor=_make_browser_set(user_id),
    )
