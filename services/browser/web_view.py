"""WebViewClient — bridges the persona's `web_view` tool to the Electron
desktop's `WebContentsView` over the HTTP shim defined in
`desktop/src/web_view_shim.ts`.

The shim writes its port + auth token to `<projectRoot>/data/web_view_port.json`
(or `WEB_VIEW_PORT_FILE` env var if set) at Electron startup. This client
reads that registry on each call so a desktop restart is picked up
without requiring a sidecar restart.

If the registry is missing, the desktop is not running. We surface that
to the persona as `{"error": "desktop_not_running"}` so it can speak the
limitation back to the user instead of erroring out the chat turn.

When `include_screenshot=True`, this client folds the screenshot through
`infra.model.vision.describe_image` and merges the description back into
the perception report under `screenshot_description`. The base64 PNG is
NOT returned to the persona — the description (text) is. Only the vision
model handles raw image bytes.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from infra.model.vision import describe_image


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REGISTRY = _PROJECT_ROOT / "data" / "web_view_port.json"


def _registry_path() -> Path:
    env = os.environ.get("WEB_VIEW_PORT_FILE")
    return Path(env) if env else _DEFAULT_REGISTRY


def _read_registry() -> Optional[dict]:
    p = _registry_path()
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


class DesktopUnavailable(Exception):
    """Raised when the Electron registry is missing or unreadable."""


class WebViewClient:
    """Async HTTP client for the desktop's web_view shim.

    Resolves the shim's port + token on every call (cheap — local file
    read) so you don't have to re-init the client when the desktop
    restarts.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        # `open` and `observe` can take >5s when include_screenshot triggers
        # the vision call (~5–6s for Doubao). 30s default covers both.
        self._timeout = timeout

    async def _post(self, route: str, payload: dict) -> dict:
        reg = _read_registry()
        if reg is None:
            raise DesktopUnavailable("web_view registry not found — is Electron running?")
        port = reg["port"]
        token = reg["token"]
        url = f"http://127.0.0.1:{port}{route}"
        headers = {"X-Web-View-Token": token, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {"error": resp.text[:500]}
        if resp.status_code >= 400 and "error" not in data:
            data["error"] = f"shim returned HTTP {resp.status_code}"
        return data

    async def open(self, url: str, include_screenshot: bool = False) -> dict:
        report = await self._post(
            "/open",
            {"url": url, "include_screenshot": include_screenshot},
        )
        return await self._enrich(report, include_screenshot)

    async def observe(self, include_screenshot: bool = False) -> dict:
        report = await self._post(
            "/observe", {"include_screenshot": include_screenshot}
        )
        return await self._enrich(report, include_screenshot)

    async def click(
        self,
        selector: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if selector:
            body["selector"] = selector
        if x is not None and y is not None:
            body["x"] = x
            body["y"] = y
        return await self._post("/click", body)

    async def type(self, text: str, selector: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"text": text}
        if selector:
            body["selector"] = selector
        return await self._post("/type", body)

    async def scroll(self, direction: str = "down", amount: int = 400) -> dict:
        return await self._post("/scroll", {"direction": direction, "amount": amount})

    async def wait_for(self, selector: str, timeout_ms: int = 5000) -> dict:
        return await self._post(
            "/wait_for", {"selector": selector, "timeout_ms": timeout_ms}
        )

    async def close(self) -> dict:
        return await self._post("/close", {})

    async def _enrich(self, report: dict, include_screenshot: bool) -> dict:
        """When the shim returned a screenshot, run it through the vision
        model and replace the raw bytes with a textual description before
        the report flows up to the (text-only) persona."""
        if not include_screenshot:
            return report
        if "error" in report:
            return report
        b64 = report.pop("screenshot_b64", None)
        if not b64:
            report["screenshot_description"] = None
            return report
        try:
            data_url = f"data:image/png;base64,{b64}"
            description = await describe_image(
                data_url,
                "Describe what is currently visible on this page in one or two short sentences. "
                "Mention any video/canvas content, error banners, loaders, or notable text.",
            )
            report["screenshot_description"] = description
        except Exception as e:
            report["screenshot_description"] = None
            report["screenshot_error"] = str(e)
        return report
