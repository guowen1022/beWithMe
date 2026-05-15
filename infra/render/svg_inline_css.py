"""Flatten an SVG's scoped <style> block into inline style attributes.

Why: `react-native-svg`'s `SvgXml` (mobile note renderer) does not honor
`<style>` blocks with `#id .class` descendant selectors that Mermaid emits.
Inline `style="..."` attributes work on every SVG renderer.

We parse the `<style>` element, match each rule's selectors against the SVG
tree, and merge the declarations onto each matched element's existing
inline style. The original `<style>` block is left intact so desktop
browsers keep their @keyframes animations and filter effects; inline
styles win specificity on both surfaces.

Mermaid's selector vocabulary is small and predictable:
    #id, #id .class, #id .class.class, #id element,
    #id element.class, #id [attr="val"].class, #id .class element,
    #id .class .class, and comma-separated selector lists.
A hand-rolled matcher covers it; no `cssselect` dependency needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from lxml import etree


# Public entry point ----------------------------------------------------------


def inline_svg_css(svg_text: str) -> str:
    """Fold each `<style>` rule into inline `style` attributes on matches.

    Best-effort: any parse error returns the original text untouched.
    The `<style>` block is kept in place; we only add inline declarations.
    """
    if "<style" not in svg_text:
        return svg_text
    parser = etree.XMLParser(remove_comments=True, recover=True)
    try:
        root = etree.fromstring(svg_text.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError:
        return svg_text
    if root is None:
        return svg_text

    style_els = root.xpath(".//*[local-name()='style']")
    if not style_els:
        return svg_text

    for style_el in style_els:
        css_text = (style_el.text or "")
        if not css_text.strip():
            continue
        for selectors_str, decls in _iter_rules(css_text):
            for sel_str in selectors_str.split(","):
                parts = _parse_complex(sel_str)
                if parts is None:
                    continue
                for match in _find_matches(root, parts):
                    _merge_inline(match, decls)

    # Strip web-only CSS from elements we didn't touch (Mermaid sets
    # `max-width:…` on the root <svg> directly, for instance).
    _filter_existing_styles(root)

    return etree.tostring(root, encoding="unicode")


# Rule extraction -------------------------------------------------------------


def _iter_rules(css: str):
    """Yield (selectors, declarations) pairs. Skips @-blocks and :root."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    css = _strip_at_blocks(css)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sels = m.group(1).strip()
        decls = m.group(2).strip()
        if not sels or not decls:
            continue
        if ":root" in sels:
            continue
        yield sels, decls


def _strip_at_blocks(text: str) -> str:
    """Drop @keyframes / @media / @-anything blocks; they don't inline."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "@":
            brace = text.find("{", i)
            if brace == -1:
                semi = text.find(";", i)
                i = (semi + 1) if semi != -1 else n
                continue
            depth = 1
            j = brace + 1
            while j < n and depth > 0:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# Selector parsing ------------------------------------------------------------


@dataclass
class _SimpleSel:
    tag: Optional[str] = None
    elem_id: Optional[str] = None
    classes: List[str] = field(default_factory=list)
    attrs: List[Tuple[str, str]] = field(default_factory=list)


def _parse_complex(s: str) -> Optional[List[_SimpleSel]]:
    """Split a selector on whitespace (descendant combinator).

    Returns None if the selector uses unsupported features (pseudo-classes,
    `>`/`+`/`~` combinators, attribute operators other than `=`).
    """
    s = s.strip()
    if not s or ":" in s or ">" in s or "+" in s or "~" in s:
        return None
    parts = [p for p in re.split(r"\s+", s) if p]
    out: List[_SimpleSel] = []
    for p in parts:
        sel = _parse_simple(p)
        if sel is None:
            return None
        out.append(sel)
    return out


_TAG_RE = re.compile(r"[A-Za-z][\w-]*")
_ID_RE = re.compile(r"#([\w-]+)")
_CLASS_RE = re.compile(r"\.([\w-]+)")
_ATTR_RE = re.compile(
    r"""\[
        ([\w-]+)
        (?:\s*=\s*
            (?:"([^"]*)"|'([^']*)'|([\w-]+))
        )?
    \]""",
    re.VERBOSE,
)


def _parse_simple(s: str) -> Optional[_SimpleSel]:
    sel = _SimpleSel()
    i, n = 0, len(s)
    # Universal `*` — treat as no tag constraint.
    if i < n and s[i] == "*":
        i += 1
    elif i < n and s[i] not in "#.[":
        m = _TAG_RE.match(s, i)
        if not m:
            return None
        sel.tag = m.group(0).lower()
        i = m.end()
    while i < n:
        ch = s[i]
        if ch == "#":
            m = _ID_RE.match(s, i)
            if not m:
                return None
            sel.elem_id = m.group(1)
            i = m.end()
        elif ch == ".":
            m = _CLASS_RE.match(s, i)
            if not m:
                return None
            sel.classes.append(m.group(1))
            i = m.end()
        elif ch == "[":
            m = _ATTR_RE.match(s, i)
            if not m:
                return None
            attr = m.group(1)
            val = m.group(2) or m.group(3) or m.group(4) or ""
            sel.attrs.append((attr, val))
            i = m.end()
        else:
            return None
    return sel


# Matching --------------------------------------------------------------------


def _local_name(el) -> str:
    return etree.QName(el.tag).localname.lower()


def _matches_simple(el, sel: _SimpleSel) -> bool:
    if sel.tag and _local_name(el) != sel.tag:
        return False
    if sel.elem_id and el.get("id") != sel.elem_id:
        return False
    if sel.classes:
        cl = (el.get("class") or "").split()
        for c in sel.classes:
            if c not in cl:
                return False
    for name, expected in sel.attrs:
        actual = el.get(name)
        if expected:
            if actual != expected:
                return False
        else:
            if actual is None:
                return False
    return True


def _find_matches(root, parts: List[_SimpleSel]):
    """Yield every element under `root` matching the full descendant chain."""
    if not parts:
        return
    # Walk every element; check rightmost match first (cheap filter).
    last = parts[-1]
    rest = parts[:-1]
    # `iter()` includes the root itself; that's fine — Mermaid's `#id`
    # selector should match the SVG root.
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue  # comments, PIs
        if not _matches_simple(el, last):
            continue
        if not rest:
            yield el
            continue
        # Walk ancestors closest-first; consume `rest` from the end
        # (closer-to-element parts of the selector match closer ancestors).
        needed = list(reversed(rest))  # e.g. ['B','A'] for 'A B C'
        idx = 0
        for anc in el.iterancestors():
            if _matches_simple(anc, needed[idx]):
                idx += 1
                if idx == len(needed):
                    break
        if idx == len(needed):
            yield el


# Style merge -----------------------------------------------------------------

# SVG presentation properties that actually do something when set inline on an
# SVG element. Mermaid's stylesheet emits a lot of web-CSS (background-color,
# text-align, max-width, padding, …) that's pointless on SVG and trips up some
# SVG renderers (notably react-native-svg's getStyle, which doesn't tolerate
# every CSS shape). Filter to this allowlist.
#
# `font-family` is excluded deliberately: Mermaid's font stacks contain quoted
# font names like `"Segoe UI"`, which serialize back through XML as
# `&quot;Segoe UI&quot;`. react-native-svg's xml parser doesn't HTML-decode
# attribute values before splitting on `;`, so the `;` *inside* `&quot;` ends
# up creating colonless declaration fragments that crash its style tokenizer.
# RN-svg can't render those web fonts on a phone anyway.
_SVG_STYLE_PROPS = frozenset({
    # paint
    "fill", "fill-opacity", "fill-rule",
    "stroke", "stroke-width", "stroke-dasharray", "stroke-dashoffset",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-opacity",
    # text (font-family intentionally omitted; see comment above)
    "font-size", "font-weight", "font-style",
    "text-anchor", "text-decoration", "letter-spacing", "word-spacing",
    "dominant-baseline", "alignment-baseline",
    # color / visibility
    "color", "opacity", "visibility", "display",
    # filters / shape
    "filter", "clip-path", "mask",
    # cursor / interaction
    "cursor", "pointer-events",
})


def _filter_declarations(decls: str) -> str:
    """Keep only SVG-valid presentation declarations from a `prop:val;…` block."""
    kept: List[str] = []
    for raw in decls.split(";"):
        decl = raw.strip().rstrip(";")
        if not decl or ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not prop or not val:
            continue
        if prop in _SVG_STYLE_PROPS:
            kept.append(f"{prop}:{val}")
    return ";".join(kept)


def _merge_inline(el, additions: str) -> None:
    """Add SVG-valid declarations from `additions` to el's existing inline style.

    Existing inline declarations win (they come last in the merged string).
    Drops `!important` markers since react-native-svg ignores them anyway
    and they confuse simple string parsing downstream. Non-SVG CSS (web-only
    layout/background) is filtered out — from BOTH our additions and any
    pre-existing inline style on the element — so SVG renderers don't see
    properties they don't understand.
    """
    additions = re.sub(r"\s*!\s*important", "", additions).strip().rstrip(";")
    additions = _filter_declarations(additions)
    if not additions:
        return
    existing = _filter_declarations((el.get("style") or "").strip().rstrip(";"))
    merged = f"{additions};{existing}" if existing else additions
    el.set("style", merged)


def _filter_existing_styles(root) -> None:
    """Walk the SVG and strip non-SVG declarations from every pre-existing
    `style="…"` attribute. Mermaid emits e.g. `max-width:…` on the root <svg>
    that we never touched via _merge_inline."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        s = el.get("style")
        if not s:
            continue
        cleaned = _filter_declarations(s)
        if cleaned != s.strip().rstrip(";"):
            if cleaned:
                el.set("style", cleaned)
            else:
                del el.attrib["style"]
