"""Stage-2 deep answering pass for `/api/ask/stream`.

Reached after the lead pass (Stage 1) gives a short spoken acknowledgment and
calls `request_handoff(target="deep")` — the turn needs looking at or acting on
something the fast, tool-less line can't reach (inspect a diagram it drew, an
image, the document, the web, re-check produced work).

Unlike the lead pass, this pass has the FULL teaching tool palette AND the
durable contents of the teacher's produced notes injected up front — so it can
actually answer "does my LRU diagram match the definition?" instead of
disclaiming. It runs detached (like `run_canvas_writer`): the originating SSE
response is already closed, so it delivers its answer out-of-band over the
persistent `/dynamic/stream` channel by reusing `AutoSpeakBuffer` → `tool_speak`
(VoicePlay audio + teacher-speech caption). It stores the real Interaction (the
deep answer is the actual Q&A turn; the lead's holding line stores nothing).
"""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Optional
from uuid import UUID

from infra.db import async_session
from infra.event_log import log_event
from infra.silicon_brain_client import SiliconBrainClient
from infra.contracts.output_routing import OUTPUT_DEVICE_ID

from persona.teacher import assemble_context, parse_title
from persona.teacher.models.interaction import Interaction
from persona.teacher.brain_builder.background import post_interaction_update
from persona.teacher.tools import build_tools as build_teacher_tools
from persona.teacher.tools.loop import run as run_teacher_tool_loop
from persona.teacher.tools.grants import TEACHER_GRANT
from persona.teacher.contexts._produced_notes import collect_produced_notes, render_full

from services.persona.routers._ask_voice import AutoSpeakBuffer


# Hold references to detached deep tasks + their auto-speak children so they
# survive GC for the lifetime of the work.
_deep_tasks: set = set()


def _deep_directive(holding_line: str) -> str:
    """Tell the deep pass it's the second stage: the user already heard a brief
    acknowledgment, so DO the work and deliver the real answer — don't
    re-acknowledge or repeat the holding line."""
    said = (holding_line or "").strip()
    said_part = f' You already said to the user: "{said}".' if said else ""
    return (
        "=== YOU ARE THE DEEP PASS ===\n"
        "This is the second stage of the turn." + said_part + " Now actually do "
        "the work the user asked for: use your tools and read the notes you drew "
        "(their full markdown is included above) to inspect/verify, then give the "
        "real answer in natural spoken prose. Do NOT re-acknowledge, do NOT say "
        "'let me check' again, and never say you can't see or access something — "
        "you have the tools and your own materials right here.\n"
        "Your answer prose is voiced to the user automatically. Write it as plain "
        "spoken sentences ONLY — never write a tool call (e.g. speak(...), "
        "mount_template(...)) into your spoken text; invoke tools through the "
        "tool-call mechanism, not as words in your reply."
    )


async def run_deep_answer(
    *,
    question: str,
    holding_line: str,
    user_id: UUID,
    body,
    active_channel: str,
    req_id: Optional[str] = None,
    origin: Optional[float] = None,
) -> None:
    """Deep answering pass — runs after the lead's holding line. Full tool
    palette + produced-note contents; delivers a spoken/captioned answer
    out-of-band and stores the real Interaction. Never raises into the caller —
    the originating SSE stream is already closed.
    """
    deep_t0 = time.perf_counter()
    timing_origin = origin if origin is not None else deep_t0
    phases: dict = {}
    answer = ""
    answer_body = ""
    extracted_title: Optional[str] = None
    usage: dict = {}
    error: Optional[str] = None

    # Optional thinking spinner on the user's canvas while the deep pass runs —
    # bridges the gap after the holding line. Best-effort.
    try:
        from infra.devices.delivery import enqueue_for_user
        from infra.contracts.ui import TeacherThinking
        await enqueue_for_user(user_id, TeacherThinking(phase="start", trigger="deep"))
    except Exception:
        pass

    autospeak = AutoSpeakBuffer(
        user_id=user_id,
        active_channel=active_channel,
        timing_origin=timing_origin,
        phases=phases,
        background_tasks=_deep_tasks,
    )

    try:
        async with async_session() as db:
            client = SiliconBrainClient()
            try:
                # Full-palette voice answering prompt (canvas + reading + look +
                # web tools), then prepend the durable contents of the teacher's
                # produced notes + the deep directive.
                ctx = await assemble_context(
                    body, user_id, db, client,
                    voice_mode=True, lead_pass=False,
                )
                produced_full = render_full(
                    collect_produced_notes(user_id, limit=5, max_age_s=6 * 3600)
                )
                preamble_parts = [p for p in (produced_full, _deep_directive(holding_line)) if p]
                if preamble_parts:
                    preamble = "\n\n".join(preamble_parts)
                    ctx.parts = ctx.parts._replace(
                        dynamic_user=f"{preamble}\n\n{ctx.parts.dynamic_user}"
                    )

                # Drop `speak` from the deep palette: this pass is auto-spoken
                # (AutoSpeakBuffer voices its streamed prose), so an in-model
                # `speak` tool is redundant — and a model that emits
                # `speak(channel=…, text=…)` as PROSE instead of a clean tool
                # call gets the raw structure read aloud. Removing it makes the
                # answer-as-prose the only voice path.
                tools = [t for t in build_teacher_tools(user_id) if t.name != "speak"]

                async for evt in run_teacher_tool_loop(
                    static_system=ctx.parts.static_system,
                    static_user_passage=ctx.parts.static_user_passage,
                    dynamic_user=ctx.parts.dynamic_user,
                    prior_messages=ctx.prior_messages,
                    tools=tools,
                    purpose="deep-answer",
                    user_id=user_id,
                    phases=phases,
                    timing_origin=timing_origin,
                    profile="voice",
                    # A real tool-using answer may read_media / look / search
                    # before it speaks — give the loop room for a few tool turns.
                    max_iterations=8,
                    grant=TEACHER_GRANT,
                ):
                    kind = evt.get("kind")
                    if kind == "delta":
                        autospeak.feed(evt.get("text", ""))
                    elif kind == "tool_call":
                        # If the deep pass speaks itself, stop auto-speak so we
                        # don't double-voice.
                        if evt.get("name") == "speak":
                            autospeak.suppress()
                    elif kind == "done":
                        answer = evt.get("text", "")
                        usage = evt.get("usage", {}) or {}

                autospeak.flush_tail()

                extracted_title, answer_body = parse_title(answer)
                if not answer_body:
                    answer_body = answer

                # Store the real Interaction — the deep answer is the Q&A turn.
                if answer_body:
                    interaction = Interaction(
                        user_id=user_id,
                        session_id=body.session_id,
                        parent_interaction_id=body.parent_interaction_id,
                        title=extracted_title,
                        passage_text=body.passage_text,
                        question=body.question,
                        answer=answer_body,
                        source_document=str(body.document_id) if body.document_id else None,
                    )
                    db.add(interaction)
                    await db.commit()
                    await db.refresh(interaction)
                    try:
                        task = asyncio.create_task(
                            post_interaction_update(interaction.id, user_id)
                        )
                        _deep_tasks.add(task)
                        task.add_done_callback(_deep_tasks.discard)
                    except Exception as e:
                        print(f"[ask/deep] post-interaction schedule failed: {e}", flush=True)
            finally:
                await client.aclose()
    except Exception as e:
        error = str(e)
        print(f"[ask/deep] error: {e}", flush=True)
        traceback.print_exc()

    try:
        from infra.devices.delivery import enqueue_for_user
        from infra.contracts.ui import TeacherThinking
        await enqueue_for_user(user_id, TeacherThinking(phase="end", trigger="deep"))
    except Exception:
        pass

    log_event(
        "ask.deep_done",
        req_id=req_id,
        user_id=str(user_id),
        wall_ms=round((time.perf_counter() - deep_t0) * 1000, 2),
        answer_len=len(answer_body),
        title=extracted_title,
        usage=usage or None,
        error=error,
    )


__all__ = ["run_deep_answer"]
