"""Smoke tests for the Mermaid SVG renderer.

Exercises five diagram types and verifies cache hits. Boots a Chromium
once per module via the shared `_PageHolder`, so total wall-clock should
be ~3–5s including cold start.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import pytest

from infra.render import mermaid as mermaid_mod
from infra.render.mermaid import render_mermaid


@pytest.fixture(scope="module", autouse=True)
def _clean_cache():
    # Each call to render runs in its own asyncio.run() loop; the
    # renderer detects loop changes and re-inits, so we don't try to
    # await close() across loops here — leak the browser to process
    # exit, which is fine for tests.
    cache = Path("data/diagrams")
    if cache.exists():
        shutil.rmtree(cache)
    yield


DIAGRAMS = [
    ("flowchart", "graph TD\nA[start]-->B[finish]"),
    ("sequence",  "sequenceDiagram\nAlice->>Bob: hi\nBob-->>Alice: hi back"),
    ("class",     "classDiagram\nclass Animal\nclass Dog\nAnimal <|-- Dog"),
    ("state",     "stateDiagram-v2\n[*] --> Idle\nIdle --> Running\nRunning --> [*]"),
    ("gantt",     "gantt\ntitle A\ndateFormat YYYY-MM-DD\nsection s\ntask :a, 2026-01-01, 1d"),
]


@pytest.mark.parametrize("name,source", DIAGRAMS, ids=[d[0] for d in DIAGRAMS])
def test_renders_diagram_type(name: str, source: str) -> None:
    svg = asyncio.run(render_mermaid(source))
    assert svg.lstrip().startswith("<svg"), f"{name}: expected leading <svg>, got: {svg[:80]!r}"
    assert "</svg>" in svg


def test_cache_returns_identical_bytes() -> None:
    src = "graph TD\nA-->B"
    first = asyncio.run(render_mermaid(src))
    second = asyncio.run(render_mermaid(src))
    assert first == second


def test_cache_is_significantly_faster_than_render() -> None:
    src = "graph TD\nN1[node]-->N2[node]"
    # Cold path (may include browser warmup if this is the first call)
    asyncio.run(render_mermaid(src))
    # Cached path — disk read only
    t0 = time.perf_counter()
    asyncio.run(render_mermaid(src))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Cache hit should be well under 50ms even on slow filesystems.
    assert elapsed_ms < 50, f"cache hit took {elapsed_ms:.0f}ms, expected <50ms"


def test_empty_source_raises() -> None:
    with pytest.raises(ValueError):
        asyncio.run(render_mermaid(""))
    with pytest.raises(ValueError):
        asyncio.run(render_mermaid("   \n  "))
