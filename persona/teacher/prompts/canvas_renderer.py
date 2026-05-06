"""Render `MediaPerception` into a prompt-ready markdown section.

Takes the persona's perception of every connected canvas + voice and
produces the `=== CURRENTLY ON CANVAS ===` block the teacher reads at
the top of every dynamic_user message. Knows the teacher's vocabulary
for each block kind (PDF reader, text panel, diagram by name, etc.) so
the LLM never has to think about block ids.

Pure function — no I/O, no LLM. Same input always produces the same
output. Extracted from `prompt_v2.py` (lines 39-302) so it can be
shared across the answer + reflect prompts and any future scenarios.
"""
from __future__ import annotations


_GRAPH_BLOCK_PREFIX = "interactive-graph"


def _diagram_name_from_block_id(block_id: str) -> str:
    """`interactive-graph` → `main`; `interactive-graph-steps` → `steps`."""
    if block_id == _GRAPH_BLOCK_PREFIX:
        return "main"
    if block_id.startswith(_GRAPH_BLOCK_PREFIX + "-"):
        return block_id[len(_GRAPH_BLOCK_PREFIX) + 1:]
    return block_id


def format_canvas_state(perc: object) -> str:
    """Render the user's current canvas + voice state as a terse
    intent-vocab section the teacher can read without thinking about
    block ids.

    `perc` is a MediaPerception (avoiding the import here to keep this
    module framework-light). Returns empty string when nothing is up.
    """
    if perc is None:
        return ""
    canvases = getattr(perc, "canvases", []) or []
    voices = getattr(perc, "voices", []) or []

    # Same block id can appear under multiple devices. Pick the most
    # informative view per block: prefer the device whose entry has
    # structured state, then the most recent update.
    best_by_id: dict = {}
    for canvas in canvases:
        if not getattr(canvas, "online", False):
            continue
        for block in getattr(canvas, "blocks", []) or []:
            bid = block.id
            cur = best_by_id.get(bid)
            if cur is None:
                best_by_id[bid] = block
                continue
            cur_has_state = getattr(cur, "state", None) is not None
            new_has_state = getattr(block, "state", None) is not None
            if new_has_state and not cur_has_state:
                best_by_id[bid] = block
                continue
            if cur_has_state and not new_has_state:
                continue
            cur_age = getattr(cur, "last_updated_s_ago", None)
            new_age = getattr(block, "last_updated_s_ago", None)
            if new_age is not None and (cur_age is None or new_age < cur_age):
                best_by_id[bid] = block

    lines: list[str] = []
    for block in best_by_id.values():
        line = _format_block_line(block)
        if line:
            lines.append(line)

    voice_lines: list[str] = []
    for voice in voices:
        if not getattr(voice, "online", False):
            continue
        utts = getattr(voice, "recent_utterances", []) or []
        if not utts:
            continue
        last = utts[-1]
        text = (getattr(last, "text", "") or "").strip().replace("\n", " ")
        if len(text) > 70:
            text = text[:67] + "…"
        voice_lines.append(f'- voice: last said "{text}"')

    if not lines and not voice_lines:
        return ""

    parts = ["=== CURRENTLY ON CANVAS ==="]
    parts.extend(lines)
    parts.extend(voice_lines)
    return "\n".join(parts)


def _format_block_line(block) -> str:
    """One line per surface, in the teacher's vocabulary (never expose block id)."""
    state = getattr(block, "state", None)
    title = getattr(block, "title", None)
    bid = block.id
    age = getattr(block, "last_updated_s_ago", None)

    # Mount-tracker can have a block on canvas before it has reported
    # state. Render an explicit "mounted, no state yet" so the teacher
    # doesn't conclude "nothing on canvas, mount it again."
    if state is None:
        if bid == "pdf-reader":
            return "- PDF reader: mounted (state report pending — a document may be loading)"
        if bid == "upload-file":
            return "- upload widget: mounted (state report pending)"
        if bid == "passage-reader":
            return "- text panel: mounted (state report pending)"
        if bid == "inputs-launcher":
            return "- launcher: mounted (state report pending)"
        if bid == _GRAPH_BLOCK_PREFIX or bid.startswith(_GRAPH_BLOCK_PREFIX + "-"):
            name = _diagram_name_from_block_id(bid)
            return f'- diagram "{name}": mounted (state report pending)'
        return f"- {title or bid}: mounted (state report pending)"

    # Diagram surface (interactive-graph instances).
    if bid == _GRAPH_BLOCK_PREFIX or bid.startswith(_GRAPH_BLOCK_PREFIX + "-"):
        name = _diagram_name_from_block_id(bid)
        if state is not None and state.kind == "graph":
            extra = state.extra or {}
            kind = extra.get("mermaid_kind") or "diagram"
            n_nodes = len(extra.get("node_ids") or [])
            sel = extra.get("selected_node_id")
            bits = [f'- diagram "{name}": {kind}, {n_nodes} nodes']
            if sel:
                bits.append(f'(selected: "{sel}")')
            grid = _format_grid_tail(state)
            if grid:
                bits.append(grid)
            tail = _format_focus_tail(state, age)
            if tail:
                bits.append(tail)
            return " ".join(bits)
        return f'- diagram "{name}" (empty)'

    # Main reading area's empty placeholder — suppress.
    if bid == "main-reader":
        if state is None or state.kind in ("snapshot", None):
            return ""

    head = None
    if state is not None:
        kind = state.kind
        extra = state.extra or {}
        content = (state.content or "").strip().replace("\n", " ")
        if len(content) > 70:
            content = content[:67] + "…"
        if kind == "pdf":
            doc_title = extra.get("document_title")
            doc_id = extra.get("document_id")
            page = extra.get("page")
            total = extra.get("total_pages")
            viewport = extra.get("viewport_text") or ""
            if not doc_id:
                head = "- PDF reader: idle (NO DOCUMENT LOADED — user must upload)"
            else:
                page_str = (
                    f"page {page} of {total}" if page and total
                    else "loading"
                )
                preview = ""
                if viewport:
                    v = viewport.strip().replace("\n", " ")
                    if len(v) > 60:
                        v = v[:57] + "…"
                    preview = f' — "{v}"'
                head = f'- PDF reader: "{doc_title or doc_id}" ({page_str}){preview}'
        elif kind == "passage":
            label = extra.get("title") or content or title or "(empty)"
            head = f'- text panel: "{label}"'
        elif kind == "browser":
            head = f"- browser: {content or '(loading)'}"
        elif kind == "upload":
            if state.completed:
                head = f"- upload widget: FILE UPLOADED ({content}) — upload step is DONE; do not ask user to upload again"
            else:
                head = "- upload widget: empty (NO FILE CHOSEN YET — user must click Choose File)"
        elif kind == "launcher":
            head = "- launcher: awaiting user's choice (Upload PDF / Paste Passage)"
        elif kind == "snapshot":
            label = title or content or bid
            head = f'- panel: "{label}"'
        else:
            head = f"- {kind} panel" + (f': "{content}"' if content else "")
    else:
        head = f'- {title or bid} (no state yet)'

    tail = _format_focus_tail(state, age) if state is not None else ""
    grid = _format_grid_tail(state) if state is not None else ""
    suffix = " ".join(p for p in (grid, tail) if p)
    line = head + (f" {suffix}" if suffix else "")

    outline_line = _format_outline_line(state) if state is not None else ""
    if outline_line:
        line = line + "\n" + outline_line
    return line


def _format_outline_line(state) -> str:
    """e.g. '    outline: 1. Introduction (p1) · 2. Background (p2) · ...'.

    Empty when state is non-pdf, has no outline, or the outline is empty.
    For long outlines (>12 entries), collapse to a count + tool hint so
    the prompt doesn't bloat.
    """
    if state is None or state.kind != "pdf":
        return ""
    outline = (state.extra or {}).get("outline")
    if not outline or not isinstance(outline, list):
        return ""
    entries: list[dict] = [e for e in outline if isinstance(e, dict) and e.get("title")]
    if not entries:
        return ""
    if len(entries) > 12:
        return (
            f"    outline: {len(entries)} sections — call "
            "read_document(action=\"outline\") for the full list"
        )
    parts = []
    for i, e in enumerate(entries, start=1):
        title = str(e.get("title", "")).strip().replace("\n", " ")
        if len(title) > 40:
            title = title[:37] + "…"
        page = e.get("page")
        if isinstance(page, int) and page > 0:
            parts.append(f"{i}. {title} (p{page})")
        else:
            parts.append(f"{i}. {title}")
    return "    outline: " + " · ".join(parts)


def _format_grid_tail(state) -> str:
    """e.g. '(at x:0 y:0 w:160 h:90)'. Empty when grid is missing."""
    g = getattr(state, "grid", None) if state is not None else None
    if g is None:
        return ""
    return f"(at x:{g.x} y:{g.y} w:{g.w} h:{g.h})"


def _format_focus_tail(state, age) -> str:
    """e.g. '(user is here, 12s ago)' / '(idle)'."""
    if state is None:
        return ""
    pieces = []
    focus = state.focus
    if focus == "active":
        pieces.append("user is here")
    elif focus == "background":
        pieces.append("idle")
    if age is not None:
        if age < 60:
            pieces.append(f"{int(age)}s ago")
        elif age < 3600:
            pieces.append(f"{int(age // 60)}m ago")
    if not pieces:
        return ""
    return "(" + ", ".join(pieces) + ")"


__all__ = ["format_canvas_state"]
