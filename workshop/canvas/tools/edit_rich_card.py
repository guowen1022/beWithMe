"""edit_rich_card — surgical, animated edits to an already-mounted rich_card.

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

The cached HTML is taken from `_rich_card_cache`; new `html` fields run
through `infra/render/rich_card.process()` so they're sanitized and any
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
from infra.render.rich_card import process as preprocess_rich_card
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user
from workshop.canvas.tools import _rich_card_cache, _template_registry


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

    `preprocess_rich_card` returns a string; we parse it back so the
    caller can splice into the tree. Empty input → empty list (caller
    decides whether that's an error)."""
    processed = await preprocess_rich_card(html)
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


# Schema for validating ops at the executor boundary. Lighter than a full
# JSON schema — pydantic is overkill for a closed op set.
_OP_REQUIREMENTS = {
    "append":          {"html"},
    "prepend":         {"html"},
    "replace_section": {"anchor_text", "html"},
    "revise":          {"target_text", "new_text"},
    "highlight":       {"target_text"},
    "arrow_to_text":   {"target_text"},
    "annotate":        {"target_text", "note"},
}


def _validate_op(op: Any) -> None:
    if not isinstance(op, dict):
        raise EditError(f"op must be an object, got {type(op).__name__}")
    kind = op.get("op")
    if kind not in _OP_REQUIREMENTS:
        raise EditError(f"unknown op {kind!r}; valid: {sorted(_OP_REQUIREMENTS)}")
    for field in _OP_REQUIREMENTS[kind]:
        if field not in op or op[field] in (None, ""):
            raise EditError(f"op {kind!r} requires field {field!r}")


async def edit_rich_card(
    *,
    user_id: UUID,
    block_id: str,
    ops: List[dict],
    target_device_id: Optional[UUID] = None,
) -> dict:
    """Apply a list of ops to an existing rich_card and fan out one
    SSE BlockMessage so the client can animate them.

    Returns `{block_id, ops_applied}` on success, `{error: ...}` on
    validation/op failure. Never raises into the caller — errors come
    back as a string the LLM can read."""
    if not isinstance(ops, list) or not ops:
        return {"error": "ops must be a non-empty list"}

    if _template_registry.template_for(block_id) != "rich_card":
        return {"error": f"block {block_id!r} is not a rich_card; mount one first"}

    current = _rich_card_cache.get(user_id, block_id)
    if current is None:
        return {
            "error": (
                f"no cached HTML for {block_id!r}. The cache is populated "
                "by mount_template / push_block_content; if this is a "
                "fresh block, mount it first."
            )
        }

    fragment = lxml_html.fragment_fromstring(current, create_parent="div")

    try:
        for op in ops:
            _validate_op(op)
            kind = op["op"]
            if kind == "append":
                await _apply_append(fragment, op["html"])
            elif kind == "prepend":
                await _apply_prepend(fragment, op["html"])
            elif kind == "replace_section":
                await _apply_replace_section(fragment, op["anchor_text"], op["html"])
            elif kind == "revise":
                _apply_revise(fragment, op["target_text"], op["new_text"])
            elif kind in ("highlight", "arrow_to_text", "annotate"):
                # Pure animation ops — no HTML mutation. Just validate
                # the target is findable so the client doesn't get an
                # orphan op.
                if not _locate_text(fragment, op["target_text"]):
                    raise EditError(
                        f"{kind}: target_text {op['target_text']!r} not "
                        f"found in card"
                    )
    except EditError as e:
        return {"error": str(e)}

    new_html = _serialize_fragment(fragment)
    _rich_card_cache.set(user_id, block_id, new_html)

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
    }


__all__ = ["edit_rich_card"]
