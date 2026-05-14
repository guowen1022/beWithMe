"""End-to-end test of the note preprocessor with real Mermaid renders.

Slow — boots a headless Chromium via Playwright. Splits into a single
module-scoped session so the browser only starts once.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from infra.render import mermaid as mermaid_mod
from infra.render.note import process


@pytest.fixture(scope="module", autouse=True)
def _clean_cache():
    cache = Path("data/diagrams")
    if cache.exists():
        shutil.rmtree(cache)
    yield


def _run(html: str) -> str:
    return asyncio.run(process(html))


def test_pipeline_resolves_diagram_and_sanitizes_around_it() -> None:
    html = """
    <div class="card card-hero">
      <h2 class="t-display">Quicksort</h2>
      <p>Pick a <mark>pivot</mark>, partition.</p>
      <div class="bw-diagram" data-src="graph TD; A[unsorted]-->B[pivot]; B-->C[left]; B-->D[right]"></div>
      <p class="t-body">Recurse on each half.</p>
      <script>alert('xss')</script>
    </div>
    """
    out = _run(html)
    # SVG inlined where the diagram was
    assert "<svg" in out
    # Sanitizer dropped the script
    assert "<script" not in out.lower()
    # Persona-authored classes survived
    assert "card-hero" in out
    assert "t-display" in out
    # data-src is removed (it's only an authoring hint)
    assert "data-src" not in out
    # data-diagram-id is the stable post-sanitize hook
    assert "data-diagram-id" in out


def test_pipeline_with_multiple_diagrams_each_renders() -> None:
    html = """
    <div class="card">
      <div class="bw-diagram" data-src="graph TD; A-->B"></div>
      <p>Then:</p>
      <div class="bw-diagram" data-src="sequenceDiagram\nAlice->>Bob: hi"></div>
    </div>
    """
    out = _run(html)
    assert out.count("<svg") == 2
    assert 'data-diagram-id="d0"' in out
    assert 'data-diagram-id="d1"' in out


def test_pipeline_drops_diagram_with_empty_source() -> None:
    html = '<div class="card"><div class="bw-diagram" data-src=""></div><p>after</p></div>'
    out = _run(html)
    assert "bw-diagram" not in out
    assert "<p>after</p>" in out


def test_pipeline_drops_diagram_on_render_failure(monkeypatch) -> None:
    async def boom(_src: str) -> str:
        raise RuntimeError("mermaid blew up")

    monkeypatch.setattr("infra.render.note.render_mermaid", boom)
    html = '<div class="card"><div class="bw-diagram" data-src="not real"></div><p>survives</p></div>'
    out = _run(html)
    assert "<svg" not in out
    assert "<p>survives</p>" in out


def test_pipeline_preserves_image_when_https_and_strips_otherwise() -> None:
    html = """
    <div class="card">
      <img class="bw-image aspect-16-9" src="https://example.com/ok.png" alt="ok"/>
      <img class="bw-image" src="http://example.com/bad.png" alt="bad"/>
    </div>
    """
    out = _run(html)
    assert "https://example.com/ok.png" in out
    assert "http://example.com/bad.png" not in out


def test_pipeline_empty_input() -> None:
    assert _run("") == ""
