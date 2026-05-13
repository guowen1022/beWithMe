"""Parity test for the rich_card class allowlist.

The Python source of truth (`infra/render/rich_card_grammar.ALLOWED_CLASSES`)
must match the class names declared in the web CSS (`.bw-card` rules in
`frontend/app/globals.css`) and the mobile StyleSheet (`STYLES` map in
`mobile/src/canvas/blocks/RichCardBlock.styles.ts`).

Both web and mobile files are written by hand; this test fails when they
drift, so a missing class on either side is caught before it ships a
broken render.

The web CSS file and the mobile styles file are added in later tasks; the
test self-skips until both exist so backend work can land first.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from infra.render.rich_card_grammar import ALLOWED_CLASSES

_ROOT = Path(__file__).resolve().parents[2]
_WEB_CSS = _ROOT / "frontend" / "app" / "globals.css"
_MOBILE_STYLES = _ROOT / "mobile" / "src" / "canvas" / "blocks" / "RichCardBlock.styles.ts"


_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _classes_from_web_css(text: str) -> set[str]:
    """Extract class names from selectors scoped to `.bw-card`.

    Strips CSS comments first (so file paths like `infra/.../rich_card.py`
    inside doc comments don't show up as classes). Then looks for every
    `.bw-card`-containing selector and collects every `.<name>` token
    inside it.
    """
    text = _CSS_COMMENT.sub("", text)
    classes: set[str] = set()
    # Match each .bw-card-anchored selector up to the opening brace or
    # a comma (selector separator).
    for block in re.findall(r"\.bw-card[^,{}]*", text):
        for cls in re.findall(r"\.([a-zA-Z][\w-]+)", block):
            if cls != "bw-card":
                classes.add(cls)
    return classes


def _classes_from_mobile_styles(text: str) -> set[str]:
    """Extract keys from the `STYLES` StyleSheet in RichCardBlock.styles.ts.

    The file has two StyleSheets — `TAG_STYLES` (tag-driven primitives)
    and `STYLES` (the class vocabulary the persona may emit). Only the
    latter is what the grammar allowlist describes.
    """
    classes: set[str] = set()
    m = re.search(
        r"export\s+const\s+STYLES\s*=\s*StyleSheet\.create\s*\(\s*\{(.*?)\}\s*\)\s*;",
        text,
        re.DOTALL,
    )
    if not m:
        return classes
    body = m.group(1)
    for km in re.finditer(r'(?m)^\s*(?:"([^"]+)"|([A-Za-z_][\w-]*))\s*:\s*\{', body):
        key = km.group(1) or km.group(2)
        classes.add(key)
    return classes


def test_web_css_class_parity() -> None:
    if not _WEB_CSS.exists():
        pytest.skip(f"{_WEB_CSS.relative_to(_ROOT)} does not exist yet")
    text = _WEB_CSS.read_text(encoding="utf-8")
    if ".bw-card" not in text:
        pytest.skip(".bw-card ruleset not yet added to globals.css")
    web_classes = _classes_from_web_css(text)
    missing_in_css = ALLOWED_CLASSES - web_classes
    extra_in_css = web_classes - ALLOWED_CLASSES
    assert not missing_in_css, f"classes declared in grammar but missing from CSS: {sorted(missing_in_css)}"
    assert not extra_in_css, f"classes declared in CSS but missing from grammar: {sorted(extra_in_css)}"


def test_mobile_styles_class_parity() -> None:
    if not _MOBILE_STYLES.exists():
        pytest.skip(f"{_MOBILE_STYLES.relative_to(_ROOT)} does not exist yet")
    text = _MOBILE_STYLES.read_text(encoding="utf-8")
    mobile_classes = _classes_from_mobile_styles(text)
    if not mobile_classes:
        pytest.skip("mobile StyleSheet.create not yet populated")
    missing_in_mobile = ALLOWED_CLASSES - mobile_classes
    extra_in_mobile = mobile_classes - ALLOWED_CLASSES
    assert not missing_in_mobile, f"classes in grammar but missing from mobile styles: {sorted(missing_in_mobile)}"
    assert not extra_in_mobile, f"classes in mobile styles but missing from grammar: {sorted(extra_in_mobile)}"
