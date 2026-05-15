"""Tests for the SVG CSS-inliner used to fold Mermaid's scoped <style>
block into per-element inline style attributes (so react-native-svg's
SvgXml on mobile gets the same look as the desktop browser)."""
from __future__ import annotations

from infra.render.svg_inline_css import inline_svg_css


def _svg(body: str, sid: str = "d_test") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" id="{sid}">'
        f"{body}"
        f"</svg>"
    )


def test_descendant_class_selector_inlined_onto_match() -> None:
    css = "#d_test .node rect { fill:#1f2020; stroke:#ccc; }"
    body = (
        f"<style>{css}</style>"
        '<g class="node"><rect/></g>'
    )
    out = inline_svg_css(_svg(body))
    # Rect inside .node picks up both declarations as inline style.
    # Output is normalized (no whitespace after ":" or ";").
    assert '<rect style="fill:#1f2020;stroke:#ccc"/>' in out


def test_element_with_class_selector() -> None:
    css = "#d_test rect.text { fill:none; stroke-width:0; }"
    body = (
        f"<style>{css}</style>"
        '<rect class="text"/>'
        "<rect/>"  # no class — should NOT be styled
    )
    out = inline_svg_css(_svg(body))
    # First rect (with class) gets the style; second doesn't.
    assert '<rect class="text" style="fill:none;stroke-width:0"/>' in out
    # The plain <rect/> must remain styleless.
    assert "<rect/>" in out


def test_attribute_selector_with_class() -> None:
    css = '#d_test [data-look="neo"].node rect { stroke:url(#g); }'
    body = (
        f"<style>{css}</style>"
        '<g class="node" data-look="neo"><rect/></g>'
        '<g class="node"><rect/></g>'  # no data-look — not styled
    )
    out = inline_svg_css(_svg(body))
    # Only the neo node's rect gets the inline style.
    assert out.count('style="stroke:url(#g)"') == 1


def test_comma_separated_selector_list_applies_to_each() -> None:
    css = (
        "#d_test .node rect, #d_test .node circle, #d_test .node path "
        "{ fill:#1f2020; }"
    )
    body = (
        f"<style>{css}</style>"
        '<g class="node"><rect/><circle/><path/></g>'
    )
    out = inline_svg_css(_svg(body))
    assert out.count('style="fill:#1f2020"') == 3


def test_at_keyframes_skipped() -> None:
    css = (
        "@keyframes dash { from { stroke-dashoffset:0; } } "
        "#d_test .edge { stroke:lightgrey; }"
    )
    body = f'<style>{css}</style><path class="edge"/>'
    out = inline_svg_css(_svg(body))
    # Edge rule still applied.
    assert 'style="stroke:lightgrey"' in out
    # No "from" or "to" leaked into any element.
    assert "stroke-dashoffset" not in out.replace(css, "")


def test_root_pseudo_skipped() -> None:
    css = "#d_test :root { --foo:bar; } #d_test .x { color:red; }"
    body = f'<style>{css}</style><g class="x"/>'
    out = inline_svg_css(_svg(body))
    assert 'style="color:red"' in out
    # No "--foo" attribute or style on any element.
    assert "--foo" not in out.replace(css, "")


def test_existing_inline_style_wins_in_merge() -> None:
    # Element starts with its own style; CSS rule adds a conflicting fill.
    css = "#d_test .x { fill:#000; stroke:#ccc; }"
    body = f'<style>{css}</style><rect class="x" style="fill:#fff"/>'
    out = inline_svg_css(_svg(body))
    # Both declarations present, but existing inline (fill:#fff) appears
    # AFTER the added one — so fill:#fff wins per CSS cascade.
    assert "fill:#fff" in out
    idx_added = out.find("fill:#000")
    idx_existing = out.find("fill:#fff")
    assert idx_added < idx_existing


def test_style_block_kept_intact_for_desktop_fallback() -> None:
    css = "#d_test .x { color:red; }"
    body = f'<style>{css}</style><g class="x"/>'
    out = inline_svg_css(_svg(body))
    # Original <style> survives so the browser can still drive @keyframes
    # and filter() effects that don't fit into the inline-style fold.
    assert "<style>" in out
    assert "color:red" in out


def test_no_style_block_returns_unchanged() -> None:
    src = _svg('<rect class="x"/>')
    assert inline_svg_css(src) == src


def test_malformed_css_does_not_crash() -> None:
    # Missing brace — should not raise.
    body = '<style>#d_test .x { color:red</style><g class="x"/>'
    out = inline_svg_css(_svg(body))
    # We don't require the rule to apply — just that we return a string.
    assert isinstance(out, str)
    assert "svg" in out


def test_important_marker_stripped() -> None:
    css = "#d_test .x { fill:#000 !important; }"
    body = f'<style>{css}</style><g class="x"/>'
    out = inline_svg_css(_svg(body))
    # !important removed from the *inline style attribute* (RN ignores
    # it; simpler downstream). It still appears in the original <style>
    # block, which is fine.
    assert 'style="fill:#000"' in out
    after_style = out.split("</style>")[1]
    assert "!important" not in after_style
