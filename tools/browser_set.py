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

from typing import Any, Optional
from uuid import UUID  # noqa: F401 — kept for symmetry with other tools

import httpx

from infra.topology import upstream_url


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


__all__ = ["browser_set"]
