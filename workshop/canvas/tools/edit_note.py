"""edit_note — surgical, animated edits to an already-mounted note.

Phase 2 of voice-leads. Replaces the "tear-down-and-mount-fresh" pattern
when the teacher wants to evolve an existing card. The tool accepts a
list of operations; each op produces a CSS-animated change client-side.

Op vocabulary (validated against `_OP_SCHEMA` below):

  structural (mutate HTML server-side):
    {op:"append",  html:"..."}                — slide-in at end of card body
    {op:"prepend", html:"..."}                — slide-in at start of card body
    {op:"replace_section", anchor_text:"...", html:"..."}
                                              — find the block-level element
                                                containing anchor_text, swap
                                                with new html (cross-fade)
    {op:"revise", target_text:"...", new_text:"..."}
                                              — replace inline text matching
                                                target_text with new_text,
                                                wrapped in <del>/<ins> with
                                                revision-changed for diff flash

  animation-only (server validates targetability; no HTML mutation):
    {op:"highlight",     target_text:"...", duration_ms?:1500}
    {op:"arrow_to_text", target_text:"...", label?:"...", direction?:"left"}
    {op:"annotate",      target_text:"...", note:"..."}

The cached HTML is taken from `_note_cache`; new `html` fields run
through `infra/render/note.process()` so they're sanitized and any
fresh Mermaid diagrams are rendered. The mutated tree is serialized as
the new cached HTML; the cache is updated.

One SSE BlockMessage fans out on `text.<block_id>.edits` with payload
`{ops: [...], new_html: "..."}`. The client subscribes, animates each
op against its DOM target, and reconciles to new_html after structural
ops settle.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional
from uuid import UUID

from lxml import etree, html as lxml_html

from infra.contracts.ui import BlockMessage
from infra.render.note import process as preprocess_note
from infra.render.note_md import (
    render_markdown as render_note_markdown,
    render_markdown_fragment as render_note_markdown_fragment,
)
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from workshop.canvas.tools import _note_cache, _note_index, _template_registry
from infra.model.tools import ToolSpec


# Block-level elements the writer can target with replace_section /
# anchor-text lookup. Skipping <span>, <a>, <mark> etc. on purpose —
# replace_section is meant for swapping a paragraph or heading, not an
# inline mention.
_BLOCK_TAGS = {"p", "h2", "h3", "h4", "div", "ul", "ol", "li"}


class EditError(ValueError):
    """Validation failure on the writer's op list. Surfaces back to the
    LLM as the tool result so it can retry."""


def _outer_card_root(fragment) -> Any:
    """Return the outermost `<div class="card ...">` to mutate inside, or
    the fragment wrapper itself if no card div is present (defensive)."""
    for node in fragment.iter():
        cls = (node.get("class") or "").split()
        if "card" in cls:
            return node
    return fragment


def _all_text(node) -> str:
    """Concatenate all descendant text + tail. Used by target_text
    matching."""
    out: List[str] = []
    if node.text:
        out.append(node.text)
    for child in node.iter():
        if child is node:
            continue
        if child.text:
            out.append(child.text)
        if child.tail:
            out.append(child.tail)
    return "".join(out)


def _locate_text(fragment, needle: str) -> bool:
    """True iff `needle` appears somewhere in the rendered text of the
    card. Used to validate animation-only ops so the client doesn't get
    handed a target it can't find."""
    if not needle:
        return False
    haystack = _all_text(fragment)
    return needle.strip() in haystack


def _find_block_containing(fragment, needle: str):
    """First block-level element whose text content contains `needle`.
    Returns the lxml element, or None."""
    if not needle:
        return None
    target = needle.strip()
    for node in fragment.iter():
        if node.tag not in _BLOCK_TAGS:
            continue
        text = _all_text(node)
        if target in text:
            return node
    return None


async def _process_new_fragment(html: str) -> List[Any]:
    """Sanitize + render diagrams in a freshly-authored fragment, returning
    a list of lxml elements ready to insert into the card tree.

    `preprocess_note` returns a string; we parse it back so the
    caller can splice into the tree. Empty input → empty list (caller
    decides whether that's an error)."""
    processed = await preprocess_note(html)
    processed = (processed or "").strip()
    if not processed:
        return []
    # Wrap so multi-root fragments parse cleanly; iterate children.
    wrapper = lxml_html.fragment_fromstring(processed, create_parent="div")
    return list(wrapper)


async def _apply_append(fragment, html: str) -> None:
    children = await _process_new_fragment(html)
    if not children:
        raise EditError("append: html produced no valid content after sanitize")
    root = _outer_card_root(fragment)
    for child in children:
        root.append(child)


async def _apply_prepend(fragment, html: str) -> None:
    children = await _process_new_fragment(html)
    if not children:
        raise EditError("prepend: html produced no valid content after sanitize")
    root = _outer_card_root(fragment)
    for i, child in enumerate(children):
        root.insert(i, child)


async def _apply_replace_section(fragment, anchor_text: str, html: str) -> None:
    target = _find_block_containing(fragment, anchor_text)
    if target is None:
        raise EditError(
            f"replace_section: no block element contains anchor_text "
            f"{anchor_text!r}"
        )
    children = await _process_new_fragment(html)
    if not children:
        raise EditError("replace_section: html produced no valid content")
    parent = target.getparent()
    if parent is None:
        raise EditError("replace_section: target has no parent (unexpected)")
    idx = list(parent).index(target)
    parent.remove(target)
    for offset, child in enumerate(children):
        parent.insert(idx + offset, child)


def _apply_revise(fragment, target_text: str, new_text: str) -> None:
    """Find the first occurrence of `target_text` in a text or tail node
    and replace it with a `<span class="revision-changed"><del>old</del>
    <ins>new</ins></span>` marker. The client animates the flip via the
    existing revision-changed class plus a one-shot pulse.
    """
    if not target_text:
        raise EditError("revise: target_text is required")
    if not isinstance(new_text, str):
        raise EditError("revise: new_text must be a string")

    target = target_text.strip()
    for node in fragment.iter():
        # Check the element's own text and each child's tail. We replace
        # only the FIRST hit and stop — the writer should pass a distinct
        # phrase if it wants a different occurrence.
        if node.text and target in node.text:
            before, _, after = node.text.partition(target)
            span = _make_revision_span(target, new_text)
            node.text = before
            span.tail = after
            node.insert(0, span)
            return
        for i, child in enumerate(node):
            if child.tail and target in child.tail:
                before, _, after = child.tail.partition(target)
                span = _make_revision_span(target, new_text)
                child.tail = before
                span.tail = after
                node.insert(i + 1, span)
                return

    raise EditError(f"revise: target_text {target_text!r} not found in card")


def _make_revision_span(old_text: str, new_text: str):
    span = etree.Element("span")
    span.set("class", "revision-changed")
    delete_el = etree.SubElement(span, "del")
    delete_el.text = old_text
    insert_el = etree.SubElement(span, "ins")
    insert_el.text = new_text
    return span


def _serialize_fragment(fragment) -> str:
    """Serialize lxml fragment back to a string, stripping the synthetic
    wrapper added by `fragment_fromstring(create_parent='div')`."""
    serialized = lxml_html.tostring(fragment, encoding="unicode")
    m = re.match(r"^<div>(.*)</div>$", serialized, re.DOTALL)
    return m.group(1) if m else serialized


# Schema for validating ops at the executor boundary. Each structural op
# accepts EITHER `md` (preferred, markdown source) OR `html` (legacy).
# At least one must be present.
_STRUCTURAL_OPS = {"append", "prepend", "replace_section", "revise"}
_ANIMATION_OPS = {"highlight", "arrow_to_text", "annotate"}
_ALL_OPS = _STRUCTURAL_OPS | _ANIMATION_OPS


def _validate_op(op: Any) -> None:
    if not isinstance(op, dict):
        raise EditError(f"op must be an object, got {type(op).__name__}")
    kind = op.get("op")
    if kind not in _ALL_OPS:
        raise EditError(f"unknown op {kind!r}; valid: {sorted(_ALL_OPS)}")
    # Per-op required fields:
    if kind in ("append", "prepend"):
        # Need content; prefer md.
        if not (op.get("md") or op.get("html")):
            raise EditError(f"op {kind!r} requires `md` (preferred) or `html`")
    elif kind == "replace_section":
        if not op.get("anchor_text"):
            raise EditError("op replace_section requires anchor_text")
        if not (op.get("md") or op.get("html")):
            raise EditError("op replace_section requires `md` (preferred) or `html`")
    elif kind == "revise":
        if not op.get("target_text"):
            raise EditError("op revise requires target_text")
        if "new_text" not in op or op["new_text"] is None:
            raise EditError("op revise requires new_text")
    elif kind in ("highlight", "arrow_to_text"):
        if not op.get("target_text"):
            raise EditError(f"op {kind} requires target_text")
    elif kind == "annotate":
        if not op.get("target_text"):
            raise EditError("op annotate requires target_text")
        if not op.get("note"):
            raise EditError("op annotate requires note")


# ---------- markdown-mode op application ----------
#
# When the cache has md, we apply ops in markdown space and re-render
# the full markdown back to HTML. This keeps the writer's authoring
# surface clean (small diffs, no escaped HTML) and makes the cached md
# the source of truth for future edits.


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _md_apply_append(md: str, op: dict) -> str:
    new = op.get("md") or op.get("html") or ""
    sep = "" if not md or md.endswith("\n\n") else ("\n" if md.endswith("\n") else "\n\n")
    return md + sep + new.strip() + "\n"


def _md_apply_prepend(md: str, op: dict) -> str:
    new = op.get("md") or op.get("html") or ""
    return new.strip() + "\n\n" + md.lstrip()


def _md_apply_replace_section(md: str, op: dict) -> str:
    """Find the first heading whose text contains `anchor_text`, replace
    everything from that heading up to (but not including) the next
    heading of equal or higher level.
    """
    anchor = (op.get("anchor_text") or "").strip()
    new = (op.get("md") or op.get("html") or "").strip()
    headings = list(_HEADING_RE.finditer(md))
    target = None
    for h in headings:
        if anchor in h.group(2):
            target = h
            break
    if target is None:
        raise EditError(
            f"replace_section: no heading contains anchor_text {anchor!r}"
        )
    level = len(target.group(1))
    # Find the next heading of level ≤ this one.
    section_end = len(md)
    for h in headings:
        if h.start() <= target.start():
            continue
        if len(h.group(1)) <= level:
            section_end = h.start()
            break
    return md[: target.start()] + new + "\n\n" + md[section_end:]


def _md_apply_revise(md: str, op: dict) -> str:
    """Replace `target_text` with a revision-marked span. Inline HTML
    survives markdown parsing because we render with html=True; the
    sanitizer permits <del> and <ins>."""
    target = op.get("target_text") or ""
    new = op.get("new_text") or ""
    if target not in md:
        raise EditError(f"revise: target_text {target!r} not found in markdown")
    marked = (
        f'<span class="revision-changed"><del>{target}</del>'
        f'<ins>{new}</ins></span>'
    )
    # Single replace — writer should pass a distinct phrase for other
    # occurrences if needed.
    return md.replace(target, marked, 1)


def _locate_in_text(haystack: str, needle: str) -> bool:
    return bool(needle) and needle.strip() in haystack


async def _edit_via_markdown(
    user_id: UUID,
    block_id: str,
    ops: List[dict],
    cached_md: str,
    cached_html: str,
    target_device_id: Optional[UUID],
) -> dict:
    """Apply ops to the cached markdown, re-render to HTML, update
    cache, fan out. Animation-only ops are echoed to the client without
    touching the md."""
    md = cached_md
    html_for_locate = cached_html or ""
    try:
        for op in ops:
            _validate_op(op)
            kind = op["op"]
            if kind == "append":
                md = _md_apply_append(md, op)
            elif kind == "prepend":
                md = _md_apply_prepend(md, op)
            elif kind == "replace_section":
                md = _md_apply_replace_section(md, op)
            elif kind == "revise":
                md = _md_apply_revise(md, op)
            elif kind in _ANIMATION_OPS:
                # target_text must be findable in the rendered html so
                # the client has something to animate.
                if not _locate_in_text(html_for_locate, op["target_text"]):
                    raise EditError(
                        f"{kind}: target_text {op['target_text']!r} not "
                        f"found in card"
                    )
    except EditError as e:
        return {"error": str(e)}

    # Re-render the full markdown so cached html stays accurate and
    # the client gets the final form.
    new_html = await render_note_markdown(md)
    _note_cache.set(user_id, block_id, html=new_html, md=md)
    _note_index.enqueue_reembed(user_id, block_id, md)

    # Convert each structural op's `md` to an HTML snippet before
    # fan-out, so the client doesn't need a markdown renderer. Use
    # `render_markdown_fragment` (no card-shell wrap) — the snippet
    # is inserted inside the existing card body.
    client_ops = []
    for op in ops:
        co = dict(op)
        if co.get("op") in ("append", "prepend", "replace_section"):
            md_src = co.get("md")
            if isinstance(md_src, str) and md_src.strip():
                co["html"] = await render_note_markdown_fragment(md_src)
                co.pop("md", None)
        client_ops.append(co)

    event = BlockMessage(
        block_id=block_id,
        topic=f"text.{block_id}.edits",
        value={"ops": client_ops, "new_html": new_html},
    )
    if target_device_id is not None:
        await enqueue_for_device(user_id, target_device_id, event)
    else:
        await enqueue_for_user(user_id, event)

    return {
        "block_id": block_id,
        "ops_applied": len(ops),
        "op_names": [op["op"] for op in ops],
        "mode": "markdown",
    }


async def edit_note(
    *,
    user_id: UUID,
    block_id: str,
    ops: List[dict],
    target_device_id: Optional[UUID] = None,
) -> dict:
    """Apply a list of ops to an existing note and fan out one
    SSE BlockMessage so the client can animate them.

    Two modes, picked by what's in the cache:
      * markdown mode (preferred, Phase 2.5+) — ops apply to the
        cached md, full markdown re-renders to HTML.
      * legacy HTML mode — ops apply directly to the cached HTML via
        lxml. Used when the cache was populated by an HTML-only
        mount or push.

    Returns `{block_id, ops_applied, op_names, mode}` on success,
    `{error: ...}` on validation/op failure. Never raises into the
    caller — errors come back as a string the LLM can read."""
    if not isinstance(ops, list) or not ops:
        return {"error": "ops must be a non-empty list"}

    if _template_registry.template_for(block_id) != "note":
        return {"error": f"block {block_id!r} is not a note; mount one first"}

    cached_md = _note_cache.get_md(user_id, block_id)
    cached_html = _note_cache.get_html(user_id, block_id)
    if cached_md is None and cached_html is None:
        return {
            "error": (
                f"no cache entry for {block_id!r}. The cache is populated "
                "by mount_template / push_block_content; if this is a "
                "fresh block, mount it first."
            )
        }

    if cached_md is not None:
        return await _edit_via_markdown(
            user_id, block_id, ops, cached_md, cached_html or "", target_device_id
        )

    # ---- Legacy HTML mode (Phase 2 path) ----
    fragment = lxml_html.fragment_fromstring(cached_html, create_parent="div")
    try:
        for op in ops:
            _validate_op(op)
            kind = op["op"]
            if kind == "append":
                await _apply_append(fragment, op.get("html") or op.get("md") or "")
            elif kind == "prepend":
                await _apply_prepend(fragment, op.get("html") or op.get("md") or "")
            elif kind == "replace_section":
                await _apply_replace_section(
                    fragment,
                    op["anchor_text"],
                    op.get("html") or op.get("md") or "",
                )
            elif kind == "revise":
                _apply_revise(fragment, op["target_text"], op["new_text"])
            elif kind in _ANIMATION_OPS:
                if not _locate_text(fragment, op["target_text"]):
                    raise EditError(
                        f"{kind}: target_text {op['target_text']!r} not "
                        f"found in card"
                    )
    except EditError as e:
        return {"error": str(e)}

    new_html = _serialize_fragment(fragment)
    _note_cache.set(user_id, block_id, html=new_html)

    event = BlockMessage(
        block_id=block_id,
        topic=f"text.{block_id}.edits",
        value={"ops": ops, "new_html": new_html},
    )
    if target_device_id is not None:
        await enqueue_for_device(user_id, target_device_id, event)
    else:
        await enqueue_for_user(user_id, event)

    return {
        "block_id": block_id,
        "ops_applied": len(ops),
        "op_names": [op["op"] for op in ops],
        "mode": "html",
    }


__all__ = ["edit_note", "build_spec"]

def _make_edit_note(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        # `_raw_arguments` fallback: same shape as mount_template — some
        # providers wrap a complete JSON object inside this string. Recover
        # if it parses to a dict; bail only on truly unparseable truncation.
        if "_raw_arguments" in args:
            raw = args["_raw_arguments"]
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        args = parsed
                    else:
                        return json.dumps({"error": "tool arguments were truncated mid-stream — retry with fewer or shorter ops"})
                except json.JSONDecodeError:
                    return json.dumps({"error": "tool arguments were truncated mid-stream — retry with fewer or shorter ops"})
            else:
                return json.dumps({"error": "tool arguments were truncated mid-stream — retry with fewer or shorter ops"})

        block_id = (args.get("block_id") or "").strip()
        if not block_id:
            return json.dumps({"error": "block_id is required"})

        ops = args.get("ops")
        if isinstance(ops, str):
            # Some providers emit nested arrays as JSON strings. Accept.
            try:
                ops = json.loads(ops)
            except json.JSONDecodeError:
                return json.dumps({"error": "ops was a string but not valid JSON"})
        if not isinstance(ops, list):
            return json.dumps({
                "error": f"ops must be a list, got {type(ops).__name__}"
            })

        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        if target_uuid is None:
            from infra.contracts.output_routing import get_output_device_id
            ctx_target = get_output_device_id()
            if ctx_target is not None:
                target_uuid = ctx_target

        import time as _t_mod
        t0 = _t_mod.perf_counter()
        try:
            result = await edit_note(
                user_id=user_id,
                block_id=block_id,
                ops=ops,
                target_device_id=target_uuid,
            )
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        wall_ms = round((_t_mod.perf_counter() - t0) * 1000, 2)

        # Log even on error so we can see misses too.
        from infra.event_log import log_event
        log_event(
            "ask.edit_note",
            user_id=str(user_id),
            block_id=block_id,
            wall_ms=wall_ms,
            ops_count=len(ops) if isinstance(ops, list) else 0,
            op_names=(result.get("op_names") if isinstance(result, dict) else None),
            error=(result.get("error") if isinstance(result, dict) else None),
        )
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="edit_note",
        description=(
            "Apply a list of animated edits to an already-mounted "
            "note. Use this — NOT `mount_template` — when an "
            "existing note is on the same topic and should "
            "EVOLVE rather than be wiped and re-mounted. The user "
            "sees each op animate in place: new content slides in, "
            "highlights pulse, revisions flash diff colors.\n"
            "\n"
            "Op types:\n"
            "  • `append`  {md: '### New section\\n\\nprose'} — "
            "add markdown at the end of the card body. Animates "
            "slide+fade-in. (Legacy `html` field still accepted.)\n"
            "  • `prepend` {md: '…'} — add at the start.\n"
            "  • `replace_section` {anchor_text, md} — find the "
            "first heading whose text contains anchor_text, replace "
            "the section (heading + body until next equal-or-higher "
            "heading) with new markdown. Animates cross-fade.\n"
            "  • `revise` {target_text, new_text} — replace inline "
            "text matching target_text with new_text, marked with "
            "<del>/<ins>. Animates revision-flash. Use for "
            "corrections: 'cuneiform was ~3200 BCE, not 4000'.\n"
            "  • `highlight` {target_text, duration_ms?} — pulse-"
            "animate matching text. NO structural change. Use when "
            "the spoken answer just referenced something already "
            "shown: 'as I said about the Sumerians'.\n"
            "  • `arrow_to_text` {target_text, label?, direction?} "
            "— float a small arrow chip pointing at the matching "
            "text. Hangs ~3s.\n"
            "  • `annotate` {target_text, note} — attach a small "
            "caption near the matching text. Persists until next "
            "edit turn.\n"
            "\n"
            "`md` fields use the same markdown grammar as "
            "`mount_template`'s `params.markdown` (## headings, "
            "**bold**, ==hi==, lists, ```mermaid fences). The server "
            "applies ops to the cached markdown and re-renders to "
            "HTML. `target_text` / `anchor_text` must match a "
            "substring of the card's visible text exactly "
            "(case-sensitive). Match against the RENDERED text, not "
            "the markdown source — strip `**`/`==` etc. when "
            "specifying.\n"
            "\n"
            "You can mix ops in one call (e.g. append a new "
            "section AND highlight a related earlier phrase in "
            "the same call). The client animates them roughly "
            "simultaneously. **Cap: 3 ops per call; at most 1 "
            "highlight per turn.**\n"
            "\n"
            "Returns {block_id, ops_applied, op_names, mode} on "
            "success, {error: '...'} on validation failure. On "
            "error, fix and retry — DO NOT fall back to "
            "mount_template, which would wipe the card."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "string",
                    "description": (
                        "Slug of the note to edit (the same slug that "
                        "was passed to `mount_template` when this note "
                        "was created, e.g. 'sumer-mesopotamia')."
                    ),
                },
                "ops": {
                    "type": "array",
                    "description": "List of operations to apply in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": [
                                    "append", "prepend", "replace_section",
                                    "revise", "highlight", "arrow_to_text",
                                    "annotate",
                                ],
                            },
                            "md": {"type": "string"},
                            "html": {"type": "string"},
                            "target_text": {"type": "string"},
                            "anchor_text": {"type": "string"},
                            "new_text": {"type": "string"},
                            "duration_ms": {"type": "integer"},
                            "label": {"type": "string"},
                            "direction": {
                                "type": "string",
                                "enum": ["left", "right", "up", "down"],
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["op"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                },
                "target_device_id": {
                    "type": "string",
                    "description": "Optional UUID; route edits to this device only.",
                },
            },
            "required": ["block_id", "ops"],
            "additionalProperties": False,
        },
        executor=_make_edit_note(user_id),
    )
