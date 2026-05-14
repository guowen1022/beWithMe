"""Single source of truth for Mermaid render appearance.

Both the server-side Playwright renderer (`infra/render/mermaid.py`) and
the in-page client renderer used by the legacy `interactive_graph` block
(`frontend/components/MermaidLoader.tsx`) should derive their settings
from here. The web client currently hard-codes its config — keep this
file in sync by hand until that's wired up (TODO.md tracks it).
"""
from __future__ import annotations

FONT_FAMILY = (
    'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
)

# Mermaid `initialize()` options. `strict` securityLevel matters server-side:
# the persona is the author and we don't want it shipping inline HTML inside
# a `click` directive. The web client uses `loose` for interactivity in
# `interactive_graph` — fine because that block ALSO renders inside the user's
# sandboxed canvas frame, not in rich_card.
MERMAID_CONFIG: dict = {
    "startOnLoad": False,
    "theme": "dark",
    "securityLevel": "strict",
    "fontFamily": FONT_FAMILY,
    # `htmlLabels: false` makes mermaid render node text as native SVG
    # <text> instead of <foreignObject><div><p>...</p></div></foreignObject>.
    # Required because (a) rich_card scrubs <foreignObject> as a defense, and
    # (b) react-native-svg on mobile can't render <foreignObject> at all —
    # so without this, labels would be invisible on both surfaces.
    # In mermaid 11+ the per-diagram `flowchart.htmlLabels` is deprecated;
    # the top-level flag is the source of truth.
    "htmlLabels": False,
    # Same tightening the web client uses so rich_card diagrams and
    # interactive_graph diagrams have matching geometry.
    "flowchart": {"padding": 4, "diagramPadding": 4},
    "sequence": {"diagramMarginX": 8, "diagramMarginY": 4},
    "gantt": {"leftPadding": 24, "topPadding": 24},
}
