"""Canonical allowlist for rich_card content.

This is the single source of truth for what HTML the persona is allowed to
emit inside a `rich_card` block. The web sanitizer (`infra/render/rich_card.py`),
the web CSS (`frontend/app/globals.css` `.bw-card` ruleset), and the mobile
renderer (`mobile/src/canvas/blocks/RichCardBlock.styles.ts`) must all stay
in sync with these sets — `tests/test_class_allowlist_parity.py` enforces it.

Why so tight: the persona is an LLM and the output is shown on the user's
canvas. Anything outside this list either (a) doesn't render the same on
RN and web, or (b) is a security risk. Markdown is still available via
`text_display` for plain prose.
"""
from __future__ import annotations

# Tags the persona may author. The preprocessor injects <svg> and its
# children separately AFTER sanitization, so they are NOT in this set.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        # structural
        "div", "section", "article", "aside", "header", "footer",
        # headings + prose
        "h1", "h2", "h3", "h4", "p", "span", "br", "hr",
        # inline emphasis / annotation
        "strong", "em", "code", "mark", "ins", "del",
        # lists
        "ul", "ol", "li",
        # link + image
        "a", "img",
        # quote
        "blockquote",
    }
)

# Per-tag attribute allowlist. `class` is managed separately by nh3 via
# `allowed_classes` (nh3 panics if `class` appears here AND classes are
# allowlisted), so we keep it out of the universal set.
ALLOWED_ATTRS: dict[str, set[str]] = {
    "*": {"id"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "div": {"data-src", "data-diagram-id"},  # bw-diagram authoring + post-sanitize id
}

# Only https. No data:, no javascript:, no http:. Outbound links and images
# both go through this.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"https"})

# The design vocabulary. Mirrored in mobile/src/canvas/blocks/RichCardBlock.styles.ts
# and in the .bw-card CSS rules. Adding a class here without adding it in
# both other places will fail the parity test.
ALLOWED_CLASSES: frozenset[str] = frozenset(
    {
        # containers
        "card", "card-hero", "card-callout", "card-compare",
        "card-timeline", "card-definition",
        "row", "col",
        "gap-sm", "gap-md", "gap-lg",
        "pad-sm", "pad-md", "pad-lg",
        # tone / accent
        "accent", "accent-soft", "muted",
        "danger", "warn", "success", "info",
        "bg-surface", "bg-surface-2", "bg-accent-soft",
        # type scale
        "t-display", "t-title", "t-body", "t-caption", "t-mono",
        "weight-bold", "weight-semi", "italic",
        # annotation
        "revision-add", "revision-remove", "revision-changed",
        # media
        "bw-diagram", "bw-image",
        "aspect-1-1", "aspect-4-3", "aspect-16-9", "aspect-3-4",
        # layout helpers
        "center", "right",
        "border", "border-top", "border-bottom",
        "round", "round-lg",
    }
)
