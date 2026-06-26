"""Preprocess + sanitize persona-authored HTML for the note block.

Pipeline:
    persona HTML
      → find <div class="bw-diagram" data-src="..."/>; render each Mermaid
        source via infra.render.mermaid.render_mermaid() and stash the SVG
        keyed by a placeholder id
      → replace each diagram div with `<div class="bw-diagram"
        data-diagram-id="N"></div>` (empty body, no data-src)
      → serialize, sanitize via nh3 with the grammar in
        infra/render/note_grammar.py
      → re-parse, find divs with data-diagram-id, splice the rendered SVG
        in as their inner content (SVGs come from our own renderer, so
        they bypass the sanitizer; mermaid is configured with
        securityLevel='strict' to prevent foreign content)
      → serialize the final HTML and return

Notes:
- We never let persona-authored `<svg>` past the sanitizer. The only SVG
  in the output is whatever our Mermaid pipeline produced.
- Inline `style` attributes are stripped. All styling goes through the
  allowed_classes list — that's how web and mobile stay visually in sync.
- URLs in `href` / `src` are restricted to https. nh3 enforces this.
"""
from __future__ import annotations

import logging
import re
from typing import Final

import nh3
from lxml import etree, html as lxml_html

from infra.render.mermaid import render_mermaid
from infra.render.note_grammar import (
    ALLOWED_ATTRS,
    ALLOWED_CLASSES,
    ALLOWED_TAGS,
    ALLOWED_URL_SCHEMES,
)
from infra.render.svg_inline_css import inline_svg_css

logger = logging.getLogger(__name__)

_DIAGRAM_CLASS = "bw-diagram"
# Defensive: even though mermaid runs with securityLevel='strict' which
# prohibits these, strip them from rendered SVG before injection.
_SVG_FORBIDDEN_TAGS: Final = ("script", "foreignObject")


def _strip_svg_unsafe(svg_text: str) -> str:
    """Belt-and-suspenders scrub on server-rendered SVG."""
    parser = etree.XMLParser(remove_comments=True, recover=True)
    try:
        root = etree.fromstring(svg_text.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError:
        return ""
    if root is None:
        return ""
    for tagname in _SVG_FORBIDDEN_TAGS:
        # Strip both namespaced and bare matches — mermaid emits SVG namespaced.
        for el in root.xpath(f"//*[local-name()='{tagname}']"):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    return etree.tostring(root, encoding="unicode")


async def process(html_str: str) -> str:
    """Resolve diagrams + sanitize. Returns final HTML ready to ship."""
    src = (html_str or "").strip()
    if not src:
        return ""

    # Parse as a fragment. `fragment_fromstring(..., create_parent=True)`
    # wraps in a synthetic <div> so we don't worry about multi-root input.
    fragment = lxml_html.fragment_fromstring(src, create_parent="div")

    # First pass: find each diagram, render it to SVG, replace the node's
    # children with empty + tag with a stable id we can refer back to.
    svg_by_id: dict[str, str] = {}
    for idx, node in enumerate(fragment.xpath(f"//div[contains(@class, '{_DIAGRAM_CLASS}')]")):
        mermaid_src = node.get("data-src") or ""
        if not mermaid_src.strip():
            # No source → drop the placeholder entirely. Persona bug; better
            # to ship a clean card than a broken empty box.
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
            continue
        try:
            svg_raw = await render_mermaid(mermaid_src)
            svg = _strip_svg_unsafe(svg_raw)
            # Mermaid's scoped <style> block doesn't survive react-native-svg
            # on mobile; fold its rules into inline style attributes so both
            # surfaces render identically. Desktop sees inline styles win
            # against the original <style> block (kept for @keyframes).
            svg = inline_svg_css(svg)
        except Exception as exc:  # noqa: BLE001 — render failure should not break the card
            # Previously the failed diagram was silently removed, which left a
            # mysteriously-empty box in a multi-diagram card (e.g. a Poll-vs-
            # WebSocket comparison where only one side rendered). Instead keep
            # the source visible as a <pre><code> fallback AND log it, so the
            # failure is never invisible and the cause is inspectable.
            logger.warning(
                "note: mermaid render failed (%s); keeping source as code fallback. src=%r",
                exc, mermaid_src,
            )
            node.attrib.clear()
            node.tag = "pre"
            for child in list(node):
                node.remove(child)
            node.text = None
            code_el = etree.SubElement(node, "code")
            code_el.text = mermaid_src
            continue
        diagram_id = f"d{idx}"
        svg_by_id[diagram_id] = svg
        # Drop data-src (it carried the raw Mermaid text — no point shipping
        # it twice) and replace children with empty.
        node.attrib.pop("data-src", None)
        node.set("data-diagram-id", diagram_id)
        for child in list(node):
            node.remove(child)
        node.text = None

    # Serialize the diagram-resolved tree back to a string. `tostring` on
    # fragment_fromstring's synthetic wrapper gives us the wrapper too —
    # strip it.
    serialized = lxml_html.tostring(fragment, encoding="unicode")
    inner = re.match(r"^<div>(.*)</div>$", serialized, re.DOTALL)
    serialized = inner.group(1) if inner else serialized

    # Sanitize with nh3 enforcing the grammar. nh3's `allowed_classes`
    # is keyed by tag name (no wildcard supported), so expand per tag.
    allowed_classes_per_tag = {tag: set(ALLOWED_CLASSES) for tag in ALLOWED_TAGS}
    sanitized = nh3.clean(
        serialized,
        tags=set(ALLOWED_TAGS),
        attributes={k: set(v) for k, v in ALLOWED_ATTRS.items()},
        allowed_classes=allowed_classes_per_tag,
        url_schemes=set(ALLOWED_URL_SCHEMES),
        strip_comments=True,
        link_rel="noopener noreferrer",
    )

    # Second pass: splice each SVG into the corresponding diagram div.
    # AFTER sanitize so the SVG (path, g, rect, …) survives untouched.
    if svg_by_id:
        def _splice(m: re.Match[str]) -> str:
            diagram_id = m.group(2)
            svg = svg_by_id.get(diagram_id, "")
            return f"{m.group(1)}{svg}"

        sanitized = re.sub(
            r'(<div\b[^>]*\bdata-diagram-id="([^"]+)"[^>]*>)',
            _splice,
            sanitized,
        )

    return sanitized
