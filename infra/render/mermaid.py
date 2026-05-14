"""Server-side Mermaid → SVG renderer with on-disk cache.

The `note` block ships pre-rendered SVG to both web and mobile so neither
surface needs to embed mermaid.js or run a WebView per diagram. The persona
authors Mermaid source inside a `<div class="bw-diagram" data-src="...">`
element; the note preprocessor calls `render_mermaid(source)` to get the
SVG and inlines it.

Why Playwright instead of mermaid-cli: playwright is already a project dep
(requirements.txt) with Chromium installed, while mermaid-cli would add a
~150MB Puppeteer install. The mermaid library is loaded from the frontend's
node_modules tree — same source mermaid-validate.mjs uses, so any version
bump there flows through here automatically.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, Page, Playwright, async_playwright

from infra.render.mermaid_theme import MERMAID_CONFIG

_log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MERMAID_UMD = _PROJECT_ROOT / "frontend" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"
_CACHE_DIR = _PROJECT_ROOT / "data" / "diagrams"


def _cache_key(source: str) -> str:
    """SHA-256 over the Mermaid source AND the active config.

    Including the config in the key means a theme/config bump invalidates
    cached SVGs automatically — no manual purge needed.
    """
    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(json.dumps(MERMAID_CONFIG, sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.svg"


class _PageHolder:
    """Lazy singleton: one Playwright browser + page with mermaid loaded.

    All renders run on the same page via `page.evaluate`, which serializes
    naturally. A render takes ~30–80ms warm; if we ever need concurrency we
    grow this into a small pool. For now one page keeps memory at ~150MB and
    avoids the cold-start cost on every call.

    Loop-aware: Playwright objects are bound to the asyncio loop that
    created them, so when tests call `asyncio.run(...)` repeatedly each
    invocation runs in a fresh loop and the cached page becomes
    unusable. We track the bound loop and re-initialize if it changes.
    Production runs inside one long-lived FastAPI loop, so the re-init
    branch never fires there.
    """

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def get(self) -> Page:
        current_loop = asyncio.get_running_loop()
        if self._page is not None and self._loop is current_loop:
            return self._page
        if self._page is not None and self._loop is not current_loop:
            # Page is bound to a stale loop (typically test teardown).
            # Can't await close() on a foreign loop — just drop the refs.
            self._playwright = None
            self._browser = None
            self._page = None
            self._loop = None
        if not _MERMAID_UMD.exists():
                raise RuntimeError(
                    f"mermaid module not found at {_MERMAID_UMD}; "
                    "run `npm install` inside frontend/ to install it"
                )
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        page.on("pageerror", lambda exc: _log.warning("mermaid page error: %s", exc))
        await page.set_content(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "</head><body><div id='render-root'></div></body></html>"
        )
        # Load the UMD bundle as a plain script — its imports are
        # already inlined by the bundler. The bundle assigns itself to
        # `window.__esbuild_esm_mermaid_nm.mermaid.default`. Stash it
        # under a stable name so `render()` doesn't have to traverse
        # that path on every call.
        await page.add_script_tag(content=_MERMAID_UMD.read_text(encoding="utf-8"))
        await page.evaluate(
            "(cfg) => { "
            "window.__mermaid = window.__esbuild_esm_mermaid_nm.mermaid.default; "
            "window.__mermaid.initialize(cfg); "
            "window.__mermaidReady = true; "
            "}",
            MERMAID_CONFIG,
        )
        await page.wait_for_function("window.__mermaidReady === true", timeout=15_000)
        self._playwright = pw
        self._browser = browser
        self._page = page
        self._loop = current_loop
        _log.info("mermaid render page warm")
        return page

    async def close(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._loop = None


_holder = _PageHolder()


async def render_mermaid(source: str) -> str:
    """Render a Mermaid source string to an inline `<svg>...</svg>` string.

    Results are cached on disk under `data/diagrams/<sha>.svg` keyed by both
    source and config — bumping `MERMAID_CONFIG` invalidates the cache.
    """
    src = (source or "").strip()
    if not src:
        raise ValueError("empty mermaid source")
    key = _cache_key(src)
    path = _cache_path(key)
    if path.exists():
        return path.read_text(encoding="utf-8")

    page = await _holder.get()
    # Unique element id per call so concurrent renders don't collide on the
    # DOM node mermaid uses internally as its scratch.
    svg = await page.evaluate(
        """async (args) => {
            const { svg } = await window.__mermaid.render(args.id, args.src);
            return svg;
        }""",
        {"src": src, "id": f"d_{key[:12]}"},
    )
    if not isinstance(svg, str) or not svg.lstrip().startswith("<svg"):
        raise RuntimeError(f"mermaid.render returned unexpected payload: {type(svg).__name__}")
    path.write_text(svg, encoding="utf-8")
    return svg


async def close() -> None:
    """Tear down the warm page. Tests call this; production lets it live."""
    await _holder.close()
