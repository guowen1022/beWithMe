"""Unit tests for the text_display template's mount-time content
substitution. Runs the pure render path (no DB, no SSE) and the
sandbox JS validator (Node subprocess) so we catch syntax breakage on
edge-case content (quotes, newlines, unicode, markdown asterisks).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from infra.sandbox import validate_block_source
from infra.templates import load_template
from workshop.canvas.tools.mount_template import _render_block_source


def _render(content: str | None) -> str:
    template = load_template("text_display")
    grid = {"x": 1, "y": 1, "w": 10, "h": 6}
    params = {"content": content} if content is not None else None
    return _render_block_source(template, "text-display", grid, params)


def test_text_display_substitutes_initial_content() -> None:
    js = _render("Romeo and Juliet is a tragedy.")
    # The placeholder is gone, replaced by a JSON string literal.
    assert "__CONTENT__" not in js
    assert '"Romeo and Juliet is a tragedy."' in js


def test_text_display_subscribes_to_per_block_topic() -> None:
    js = _render("hi")
    # The bus wiring lives in the rendered JS body — both the
    # `subscribes:` array and the `bus.subscribe(...)` call must use the
    # per-block topic, NOT the raw placeholder. (The manifest JSON blob
    # injected by _render_block_source preserves the placeholder string
    # verbatim — that's a shared-with-all-templates quirk and isn't used
    # for routing, so we assert the body, not the entire source.)
    assert "subscribes: ['text.text-display.content']" in js
    assert "bus.subscribe('text.text-display.content'" in js


def test_text_display_default_when_no_content() -> None:
    js = _render(None)
    # Should still substitute __CONTENT__ — to an empty string literal —
    # so the rendered JS is parseable even if the caller forgot params.
    assert "__CONTENT__" not in js
    assert 'var initial = "";' in js


@pytest.mark.parametrize("content", [
    'a "quoted" word',
    "line one\nline two",
    "back\\slash",
    "smart “quotes” and emoji \U0001F389",
    "</script><script>alert(1)</script>",
])
def test_text_display_handles_tricky_content(content: str) -> None:
    """Quotes, newlines, backslashes, unicode, and HTML injection
    attempts must all round-trip through json.dumps + sandbox validation
    without breaking the rendered JS."""
    js = _render(content)
    # Every literal character of the JSON-encoded form should appear
    # somewhere in the rendered JS.
    encoded = json.dumps(content)
    assert encoded in js
    # And the sandbox must still parse + structure-check it. (Skips
    # silently if Node isn't available; that's acceptable per
    # validate_block_source's contract.)
    err = asyncio.run(validate_block_source(js))
    assert err is None, f"sandbox rejected rendered text_display: {err}"
