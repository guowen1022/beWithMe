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
    # Same tightening the web client uses so rich_card diagrams and
    # interactive_graph diagrams have matching geometry.
    "flowchart": {"padding": 4, "diagramPadding": 4},
    "sequence": {"diagramMarginX": 8, "diagramMarginY": 4},
    "gantt": {"leftPadding": 24, "topPadding": 24},
}
