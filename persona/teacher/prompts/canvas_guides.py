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
import re
from typing import Dict, List, Optional, Set

from infra.skillforge_client import tuned_text
from persona.teacher.prompts.skills import load_skill


MAX_GUIDE_DEPTH = int(os.environ.get("CANVAS_GUIDE_MAX_DEPTH", "1"))

# skillforge selection-tunable: the visual-guide menu the canvas-writer picks
# from. `resolve().config` may narrow/reorder/relabel it (bounded, fail-open);
# with skillforge off the menu is today's baseline, byte-for-byte.
MENU_TUNABLE_ID = "skill_menu.canvas_guides"
_SUMMARY_MAX = 240

_MENU_PREAMBLE = "Available visual guides (call `load_guide(['<id>'])` to open):"
_MENU_FOOTER = "Open only the one this turn needs, then author the fence it documents."
_FENCE_RE = re.compile(r"^```(plot|mermaid)\b", re.MULTILINE)


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


def _node_summary(nid: str, config: dict) -> str:
    """A node's one-line menu summary, with an optional bounded tuned override."""
    base = str(GUIDE_TREE[nid]["summary"])
    summaries = config.get("summaries")
    if isinstance(summaries, dict):
        val = summaries.get(nid)
        if isinstance(val, str) and 0 < len(val) <= _SUMMARY_MAX:
            return val
    return base


def _offered_ids(config: dict) -> List[str]:
    """Root menu ids after a tuned `offer` (subset) and `order` (permutation).
    Both are bounded to the registered node set — a variant may drop or reorder
    nodes but never inject one the tree doesn't define. Empty/garbage offer
    falls back to the baseline roots (fail-open)."""
    roots = _root_ids()
    offer = config.get("offer")
    if isinstance(offer, list):
        ids = [i for i in offer if i in roots]
        if not ids:
            ids = roots
    else:
        ids = list(roots)
    order = config.get("order")
    if isinstance(order, list):
        ranked = [i for i in order if i in ids]
        ids = ranked + [i for i in ids if i not in ranked]
    return ids


def _menu_lines(ids: List[str], config: Optional[dict] = None) -> str:
    config = config or {}
    rows = []
    for nid in ids:
        if nid in GUIDE_TREE:
            rows.append(f"  • {nid} — {_node_summary(nid, config)}")
    return "\n".join(rows)


def render_root_menu(config: Optional[dict] = None) -> str:
    """The visual-guide menu appended to the canvas-writer's system prompt.

    `config` is the bounded `skill_menu.canvas_guides` tunable: `offer`/`order`
    narrow and reorder the menu, `summaries` relabel a node, `select_prompt`
    overrides the lead-in — each fail-open to the baseline. `None` (skillforge
    off) reproduces today's menu byte-for-byte."""
    config = config or {}
    preamble = tuned_text(config, "select_prompt", _MENU_PREAMBLE)
    return f"{preamble}\n{_menu_lines(_offered_ids(config), config)}\n{_MENU_FOOTER}"


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


def authored_modalities(text: str) -> Set[str]:
    """Which guide modalities the writer actually authored, detected from the
    opening fence info-string (```plot / ```mermaid — the langs note_md
    renders). Only known menu ids are returned."""
    if not text:
        return set()
    return set(_FENCE_RE.findall(text)) & set(GUIDE_TREE)


def menu_outcome(selected, authored):
    """Map a canvas-writer turn to a skillforge outcome for the menu tunable.

    Returns ``(emit, ok, outcome_scalar)``:
      * no guide selected           → ``(False, ...)`` — menu unused; don't attribute
      * selected but no fence drawn  → ``(True, False, None)`` — peeked then answered in
                                        prose; NEUTRAL (don't punish correct restraint)
      * authored ⊆ selected          → ``(True, True, 1.0)`` — the pick paid off
      * authored ⊄ selected          → ``(True, True, 0.0)`` — drew a modality it never opened
    """
    selected = set(selected) & set(GUIDE_TREE)
    authored = set(authored) & set(GUIDE_TREE)
    if not selected:
        return (False, False, None)
    if not authored:
        return (True, False, None)
    if authored <= selected:
        return (True, True, 1.0)
    return (True, True, 0.0)


__all__ = [
    "MAX_GUIDE_DEPTH",
    "MENU_TUNABLE_ID",
    "GUIDE_TREE",
    "render_root_menu",
    "get_guide",
    "authored_modalities",
    "menu_outcome",
]
