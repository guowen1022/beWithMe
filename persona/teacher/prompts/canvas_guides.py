"""Canvas-writer visual-guide registry — the lazy skill tree.

The canvas-writer (Layer-2 implementer) sees only a thin MENU of visual
guides in its system prompt (`render_root_menu()`). When a claim needs a
diagram or plot, it calls the `load_guide` tool — whose executor is
`get_guide(ids)` — to pull the matching leaf's full fence syntax into
context. Only the chosen modality's guidance is ever loaded; a flowchart
turn never pays for the plot syntax and vice-versa.

Today the tree is FLAT — `plot` and `mermaid`, each a *pure implementation
skill*. `MAX_GUIDE_DEPTH` bounds descent; the machinery already supports
deeper trees (a node id `"a/b"` is depth 2) for later phases. The writer
chooses the modality for now; in the next phase the teacher hands the tool
down with a recipe and the implementer loads the named skill directly.
"""
from __future__ import annotations

import os
from typing import Dict, List

from persona.teacher.prompts.skills import load_skill


MAX_GUIDE_DEPTH = int(os.environ.get("CANVAS_GUIDE_MAX_DEPTH", "1"))


# Guide tree: node id → {summary (one menu line), skill (qualified skill name
# whose body IS the guide), children (deeper node ids, opened on demand)}.
GUIDE_TREE: Dict[str, Dict[str, object]] = {
    "plot": {
        "summary": "coordinate/numeric — scatter, curves, 3D surfaces, data-and-fit pictures",
        "skill": "teacher/canvas_writer_plot",
        "children": [],
    },
    "mermaid": {
        "summary": "structural — flowcharts, sequences, hierarchies, comparisons, bar/line charts",
        "skill": "teacher/canvas_writer_mermaid",
        "children": [],
    },
}


def _root_ids() -> List[str]:
    """Root nodes = those that are not a child of any other node."""
    child_ids = {c for n in GUIDE_TREE.values() for c in (n.get("children") or [])}
    return [nid for nid in GUIDE_TREE if nid not in child_ids]


def _depth(node_id: str) -> int:
    return node_id.count("/") + 1


def _menu_lines(ids: List[str]) -> str:
    rows = []
    for nid in ids:
        node = GUIDE_TREE.get(nid)
        if node:
            rows.append(f"  • {nid} — {node['summary']}")
    return "\n".join(rows)


def render_root_menu() -> str:
    """The visual-guide menu appended to the canvas-writer's system prompt."""
    return (
        "Available visual guides (call `load_guide(['<id>'])` to open):\n"
        f"{_menu_lines(_root_ids())}\n"
        "Open only the one this turn needs, then author the fence it documents."
    )


def get_guide(ids) -> str:
    """`load_guide` executor: return the full body of each requested guide
    node plus a menu of its children (so the model can descend). Unknown ids
    and over-depth requests degrade to a short note rather than failing the
    turn — the writer can still author with what it has."""
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not ids:
        return 'load_guide: pass `ids`, e.g. {"ids": ["plot"]}.'

    parts: List[str] = []
    for raw in ids:
        nid = str(raw).strip()
        node = GUIDE_TREE.get(nid)
        if node is None:
            parts.append(
                f"=== {nid} ===\n(no such guide; available: {', '.join(_root_ids())})"
            )
            continue
        if _depth(nid) > MAX_GUIDE_DEPTH:
            parts.append(
                f"=== {nid} ===\n(max guide depth reached — author now with what you have)"
            )
            continue
        body = load_skill(node["skill"]) or "(guide body missing)"
        block = f"=== GUIDE: {nid} ===\n{body.strip()}"
        children = [
            c for c in (node.get("children") or []) if _depth(c) <= MAX_GUIDE_DEPTH
        ]
        if children:
            block += "\n\nDEEPER (load_guide to open):\n" + _menu_lines(children)
        parts.append(block)
    return "\n\n".join(parts)


__all__ = ["MAX_GUIDE_DEPTH", "GUIDE_TREE", "render_root_menu", "get_guide"]
