"""Headless-render tests for text_display.

These tests exercise the FULL pipeline that produces a user-visible
text_display block:

  1. `_render_block_source` substitutes the template's placeholders
     (block id, grid, content, content topic).
  2. The rendered JS is eval'd in `scripts/block-run-text-display.mjs`,
     a Node script that mocks the browser DOM + provides a real
     `marked`-backed `helpers.markdown`.
  3. The script writes the body element's `innerHTML` to stdout.

Each test asserts that a markdown payload renders to the expected HTML
shapes — so a regression in either layer (Python substitution OR the
inline JS that calls `helpers.markdown`) shows up here, not on a
user's screen.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from infra.templates import load_template
from workshop.canvas.tools.mount_template import _render_block_source


_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "scripts" / "block-run-text-display.mjs"


def _node_available() -> bool:
    return shutil.which("node") is not None


def _render_html(content: str) -> str:
    """Render text_display with `content` and return the body HTML."""
    template = load_template("text_display")
    grid = {"x": 1, "y": 1, "w": 10, "h": 6}
    js = _render_block_source(template, "text-display", grid, {"content": content})
    proc = subprocess.run(
        ["node", str(_RUNNER)],
        input=js,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"runner failed: rc={proc.returncode} stderr={proc.stderr!r}")
    return proc.stdout


pytestmark = pytest.mark.skipif(
    not _node_available() or not _RUNNER.exists(),
    reason="node runtime or block-run-text-display.mjs not available",
)


def test_renders_gfm_table() -> None:
    md = (
        "| Title | Theme |\n"
        "|---|---|\n"
        "| Romeo and Juliet | love |\n"
        "| Hamlet | revenge |\n"
    )
    html = _render_html(md)
    assert "<table>" in html and "</table>" in html
    assert "<thead>" in html and "<tbody>" in html
    assert "<th>Title</th>" in html
    assert "<td>Romeo and Juliet</td>" in html


def test_renders_headings_and_lists() -> None:
    md = (
        "## Where to start\n"
        "\n"
        "- Romeo and Juliet — love\n"
        "- Hamlet — revenge\n"
        "\n"
        "1. First\n"
        "2. Second\n"
    )
    html = _render_html(md)
    assert "<h2>Where to start</h2>" in html
    assert "<ul>" in html and "<li>Romeo and Juliet — love</li>" in html
    assert "<ol>" in html and "<li>First</li>" in html


def test_renders_inline_emphasis_and_code() -> None:
    md = "**Bold** and *italic* and `code`."
    html = _render_html(md)
    assert "<strong>Bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html


def test_renders_fenced_code_block() -> None:
    md = "```python\nprint('hi')\n```\n"
    html = _render_html(md)
    # marked emits <pre><code> for fenced blocks.
    assert "<pre>" in html and "<code" in html
    assert "print(&#39;hi&#39;)" in html or "print('hi')" in html


def test_renders_blockquote() -> None:
    md = "> A wise quote.\n"
    html = _render_html(md)
    assert "<blockquote>" in html
    assert "A wise quote." in html


def test_renders_link_with_attributes() -> None:
    md = "See [the play](https://example.com/hamlet) for more."
    html = _render_html(md)
    assert 'href="https://example.com/hamlet"' in html
    assert ">the play</a>" in html


def test_html_in_input_is_escaped() -> None:
    """Persona prose must NOT be able to inject script tags. marked's
    default behavior escapes raw HTML; this test pins that contract."""
    md = "Hello <script>alert('xss')</script> world."
    html = _render_html(md)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_content_does_not_crash() -> None:
    html = _render_html("")
    # Just needs to not raise; HTML can be empty or a single empty <p>.
    assert isinstance(html, str)
