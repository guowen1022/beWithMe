"""Addressee routing for `/api/ask/stream` — the early-return paths that bypass
the teacher: the `/block` engineer shortcut and the `app_operator` persona.

`route_addressee()` returns a ready `StreamingResponse` when the request targets
one of these addressees, or `None` to fall through to the teacher path. Extracted
from `ask.py` (F6) so the router's teacher flow reads clean. Behavior is verbatim.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional
from uuid import UUID

from fastapi.responses import StreamingResponse

from infra.contracts.ui import BlockSpec
from infra.templates import list_templates, load_template
from persona.app_operator import respond as app_operator_respond
from infra.contracts.ask import AskRequest
from workshop.canvas.tools.mount_template import mount_template
from workshop.canvas.tools.request_ui_block import request_ui_block


# Explicit override: '/block <description>' routes straight to the
# request_ui_block tool, skipping the LLM router. Useful for testing and
# for users who know exactly what they want.
_BLOCK_TRIGGER = re.compile(r"^\s*/block(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


def _match_template(description: str) -> str | None:
    """If the user's `/block <description>` names an existing template
    (by filename, kebab id, or first-word keyword match), return the
    template name to mount directly. Skips the engineer LLM round-trip
    for known-good widgets like ambient_mic and pdf_reader.
    """
    if not description:
        return None
    norm = description.strip().lower().replace("-", " ").replace("_", " ")
    norm_compact = norm.replace(" ", "")
    available = list_templates()
    # Exact name / kebab match first.
    for name in available:
        candidates = {
            name.lower(),
            name.lower().replace("_", " "),
            name.lower().replace("_", "-"),
            name.lower().replace("_", ""),
        }
        if norm in candidates or norm_compact in candidates:
            return name
    # Substring match: every space-separated token in the description must
    # appear in the template name OR in its declared keywords. Avoids
    # matching "I want to upload a file" → upload_file unintentionally,
    # but lets "ambient mic" → ambient_mic.
    user_tokens = [t for t in norm.split() if t]
    if not user_tokens:
        return None
    for name in available:
        try:
            tpl = load_template(name)
        except Exception:
            continue
        haystack = (
            name.lower().replace("_", " ")
            + " "
            + " ".join(k.lower() for k in tpl.manifest.keywords)
        )
        if all(tok in haystack for tok in user_tokens):
            return name
    return None


async def _block_trigger_stream(description: str, user_id: UUID):
    """Synthetic SSE flow for any block-build delegation.

    Emits status → token (engineer LLM stream) → token (summary) → answer.
    The engineer's raw output (plan lines + FILES block) streams through
    as `token` events so the canvas debug panel can show the model's
    thinking live. No Interaction is stored — this is a tool invocation,
    not a Q&A turn. The teacher's tree picks up nothing here; the visible
    effect is the block(s) appearing on the canvas.

    Fast path: if `description` names a known template (e.g. "ambient mic"
    → ambient_mic.{md,js}), call `mount_template` directly and skip the
    engineer LLM. Saves the round-trip and guarantees the canonical block
    source instead of an LLM rewrite.
    """
    def fmt(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    template_name = _match_template(description)
    if template_name:
        yield fmt({
            "type": "status", "status": "thinking",
            "detail": f"mounting template '{template_name}'",
        })
        try:
            result = await mount_template(user_id=user_id, template_name=template_name)
            message = f"Mounted '{result.block_id}' from template {template_name}."
            yield fmt({"type": "title", "title": f"Block: {result.block_id}"})
            yield fmt({"type": "token", "text": message})
            yield fmt({
                "type": "answer",
                "answer": message,
                "title": f"Block: {result.block_id}",
                "related_interaction_ids": [],
            })
        except Exception as e:
            err = f"failed to mount template {template_name}: {e}"
            print(f"[ask/block-trigger] {err}", flush=True)
            yield fmt({"type": "token", "text": err})
            yield fmt({
                "type": "answer",
                "answer": err,
                "title": "Block: error",
                "related_interaction_ids": [],
            })
        return

    # Bridge the engineer's async-callback stream through an asyncio.Queue
    # so we can interleave its deltas into our SSE generator.
    delta_queue: asyncio.Queue = asyncio.Queue()

    async def push_delta(text: str) -> None:
        await delta_queue.put(text)

    yield fmt({"type": "status", "status": "thinking", "detail": "delegating to frontend_engineer"})
    try:
        async def run_engineer():
            try:
                return await request_ui_block(
                    BlockSpec(description=description),
                    user_id,
                    on_delta=push_delta,
                )
            finally:
                await delta_queue.put(None)

        engineer_task = asyncio.create_task(run_engineer())
        while True:
            chunk = await delta_queue.get()
            if chunk is None:
                break
            yield fmt({"type": "token", "text": chunk})
        blocks = await engineer_task
        ids = [b.id for b in blocks]
        if len(ids) == 1:
            title = f"Block: {ids[0]}"
            message = f"Mounted block '{ids[0]}' on canvas."
        else:
            title = f"Blocks: {', '.join(ids)}"
            message = f"Mounted {len(ids)} blocks: {', '.join(ids)}."
        yield fmt({"type": "title", "title": title})
        yield fmt({"type": "token", "text": message})
        yield fmt({
            "type": "answer",
            "answer": message,
            "title": title,
            "related_interaction_ids": [],
        })
    except Exception as e:
        err = f"failed to build block: {e}"
        print(f"[ask/block-trigger] {err}", flush=True)
        yield fmt({"type": "token", "text": err})
        yield fmt({
            "type": "answer",
            "answer": err,
            "title": "Block: error",
            "related_interaction_ids": [],
        })


async def _app_operator_stream(question: str, user_id: UUID):
    """Synthetic SSE flow for an app_operator turn.

    Routes the message to the app_operator persona (the "app actions"
    persona: switch_user, go_home, show_mirror) instead of the teacher.
    The persona picks one tool and fires it; the visible effect is the
    app-action SSE event (or the mounted Mirror) landing on the canvas.
    Emits status → token (persona stream) → answer, matching the shape the
    canvas command bar consumes. Like `/block`, this stores no Interaction —
    it's a tool invocation, not a Q&A turn.
    """
    def fmt(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    yield fmt({"type": "status", "status": "thinking", "detail": "delegating to app_operator"})
    answer_parts: list[str] = []
    tools_called: list[str] = []
    try:
        async for evt in app_operator_respond(question, user_id):
            kind = evt.get("kind")
            if kind == "delta":
                text = evt.get("text", "")
                if text:
                    answer_parts.append(text)
                    yield fmt({"type": "token", "text": text})
            elif kind == "tool_call":
                name = evt.get("name") or ""
                if name:
                    tools_called.append(name)
                    yield fmt({"type": "status", "status": "acting", "detail": name})
        answer = "".join(answer_parts).strip() or (
            ("Done: " + ", ".join(tools_called)) if tools_called
            else "No matching app action."
        )
        title = ("app_operator: " + ", ".join(tools_called)) if tools_called else "app_operator"
        yield fmt({"type": "title", "title": title})
        yield fmt({
            "type": "answer",
            "answer": answer,
            "title": title,
            "related_interaction_ids": [],
        })
    except Exception as e:
        err = f"app_operator failed: {e}"
        print(f"[ask/app-operator] {err}", flush=True)
        yield fmt({"type": "token", "text": err})
        yield fmt({
            "type": "answer",
            "answer": err,
            "title": "app_operator: error",
            "related_interaction_ids": [],
        })


def route_addressee(
    question: str, body: AskRequest, user_id: UUID,
) -> Optional[StreamingResponse]:
    """If the request targets a non-teacher addressee, return its SSE response;
    otherwise `None` (fall through to the teacher path).

      '/block <desc>'        → frontend_engineer (or a direct template mount)
      addressee=app_operator → the app_operator persona

    Both are early returns — not teacher Q&A turns — so they skip engagement
    emission, channel resolution, and context assembly.
    """
    trigger = _BLOCK_TRIGGER.match(question)
    if trigger:
        description = (trigger.group(1) or "").strip()
        return StreamingResponse(
            _block_trigger_stream(description, user_id),
            media_type="text/event-stream",
        )
    if getattr(body, "addressee", None) == "app_operator":
        return StreamingResponse(
            _app_operator_stream(question, user_id),
            media_type="text/event-stream",
        )
    return None
