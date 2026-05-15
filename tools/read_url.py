"""read_url — convenience shortcut: `browser_set(goto) + close`.

The most common URL-handling pattern is "load page, capture text +
XHRs, close". `read_url` packages that into a single tool call so the
persona doesn't need to do two iterations of the tool loop. Returns
`{url, title, text, length, truncated, responses}` for backward compat.

For interactive flows (click, fill, observe over time, screenshots,
evaluate JS), use `browser_set` directly. For visible browsing the
user can interact with, use `web_view`.
"""
from __future__ import annotations

from uuid import UUID

from tools.browser_set import browser_set
from infra.model.tools import ToolSpec


_MAX_TEXT_RETURN = 12_000  # chars; bound so a long page doesn't blow up persona context


async def read_url(*, user_id: UUID, url: str) -> dict:
    """Load `url` headlessly, return its text + captured XHRs, then close.

    Returns {url, title, text, length, truncated, responses} on success,
    or {error: "..."} on load / extraction failure.
    """
    if not url or not url.strip():
        return {"error": "url is required"}

    open_result = await browser_set(user_id=user_id, action="goto", url=url)
    if "error" in open_result:
        # Best-effort cleanup if the page somehow opened before erroring.
        try:
            await browser_set(user_id=user_id, action="close")
        except Exception:
            pass
        return open_result

    # One-shot semantics: close the session immediately so subsequent
    # calls start fresh. Failure to close is non-fatal — `goto` will
    # close-and-reopen on the next call anyway.
    try:
        await browser_set(user_id=user_id, action="close")
    except Exception:
        pass

    text = open_result.get("text") or ""
    truncated = False
    if len(text) > _MAX_TEXT_RETURN:
        text = text[:_MAX_TEXT_RETURN]
        truncated = True

    return {
        "url": open_result.get("url") or url,
        "title": open_result.get("title") or "",
        "text": text,
        "length": len(text),
        "truncated": truncated,
        "responses": open_result.get("responses") or [],
    }


__all__ = ["read_url", "build_spec"]

def _make_read_url(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        url = (args.get("url") or "").strip()
        if not url:
            return json.dumps({"error": "url is required"})
        try:
            result = await read_url(user_id=user_id, url=url)
        except Exception as e:
            return json.dumps({"error": f"read_url failed: {e}"})
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="read_url",
        description=(
            "Convenience shortcut: browser_set(goto) + close. One-shot "
            "headless read of a URL — no popup, no canvas mutation, no "
            "visible window. Loads the URL in headless Chromium, "
            "captures the visible text + the XHR/fetch responses the "
            "page made during load, then closes the page. Returns "
            "{url, title, text, length, truncated, responses}. Use "
            "this for the common 'what's on this URL' pattern; for "
            "anything more (interaction, observation over time, "
            "screenshots, evaluating JS) use browser_set directly. "
            "Do NOT echo the URL or raw text back at the user — "
            "extract meaning, then respond via speak."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to read.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        executor=_make_read_url(user_id),
    )
