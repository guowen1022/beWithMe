import asyncio
import json
import os
import re
import time
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from infra.db import get_db, async_session
from infra.contracts.ui import BlockSpec
from infra.event_log import log_event
from persona.teacher.models.interaction import Interaction
from persona.teacher.schemas import AskRequest, AskResponse
from persona.teacher import assemble_context, parse_title
from infra.silicon_brain_client import SiliconBrainClient
from persona.teacher.writer import run_canvas_writer
from infra.model.llm import generate_cached
from persona.teacher.brain_builder.background import post_interaction_update
from infra.auth import parse_user_id as get_current_user_id
from persona.teacher.tools import build_tools as build_teacher_tools
from persona.teacher.tools.loop import run as run_teacher_tool_loop
from workshop.canvas.tools.request_ui_block import request_ui_block
from workshop.canvas.tools.mount_template import mount_template
from infra.templates import list_templates, load_template
from tools.speak import speak as tool_speak
from infra.contracts.output_routing import OUTPUT_DEVICE_ID

router = APIRouter()

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

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()

# Sentence terminator detector for auto-speak. Mirrors
# `services/speak/main.py:_SENTENCE_SPLIT` so the boundary the client
# would split on matches what we fire here. We additionally accept
# end-of-string when flushing the buffer at stream close.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)")

_VALID_DEVICE_CLASSES = {"desktop", "tablet", "phone"}


def _resolve_active_channel(
    talk_preference: dict | None, device_class: str
) -> str:
    """Map (talk_preference, device_class) → 'voice' | 'text' | 'both'.

    Mirrors the LLM's TALK CHANNEL RULE so the backend can pick the
    right prompt builder + auto-speak behavior without round-tripping
    through the model. Defaults to 'both' if unset, matching
    `preferences_block._DEFAULT_TALK_PREF`.
    """
    fallback = {"desktop": "both", "tablet": "both", "phone": "text"}
    pref = talk_preference if isinstance(talk_preference, dict) else {}
    return pref.get(device_class) or fallback.get(device_class, "both")


def _strip_for_speech(text: str) -> str:
    """Cheap markdown stripper for auto-spoken sentences.

    The voice-mode prompt tells the LLM not to emit markdown, but the
    model occasionally leaks `**bold**` or stray `*` from training
    bias. Strip the obvious tokens so the TTS doesn't read them aloud
    ("asterisk asterisk bold"). Keep this conservative — we only
    remove characters that are clearly markdown noise.
    """
    out = text.replace("**", "").replace("__", "")
    # Drop leading/trailing whitespace + standalone bullet markers
    out = re.sub(r"^[\s>*\-]+", "", out)
    return out.strip()


def _get_client(request: Request) -> SiliconBrainClient:
    client = getattr(request.app.state, "brain_client", None)
    if client is None:
        client = SiliconBrainClient()
        request.app.state.brain_client = client
    return client


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




@router.post("/ask/stream")
async def ask_stream(
    body: AskRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    x_device_class: str | None = Header(default=None, alias="X-Device-Class"),
    x_lane_thinking: str | None = Header(default=None, alias="X-Lane-Thinking"),
    x_output_device_id: str | None = Header(default=None, alias="X-Output-Device-Id"),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
):
    """SSE endpoint with streaming — detects proxy search events.

    `X-Lane-Thinking` header: opt-in toggle for the orchestration
    benchmark. Values: `on` keeps DeepSeek thinking enabled for this
    turn (the default for Lane A is `off` for fast voice replies);
    `off` or absent uses the default. Not a public API — debug-only.
    """
    question = body.question or ""

    # Cross-device output routing. Explicit `X-Output-Device-Id` wins
    # (phone picker → desktop, etc.); otherwise default to the requesting
    # device. The old fallback (None → enqueue_for_user broadcast) leaked
    # auto-spoken answers to every signed-in device — a phone turn would
    # also play on the desktop. Routing to the requester is the only
    # sensible default for an interactive turn; persona tool calls that
    # explicitly pass target_device_id still win.
    target_raw = x_output_device_id or x_device_id
    output_device_uuid: Optional[UUID] = None
    if target_raw:
        try:
            output_device_uuid = UUID(target_raw.strip())
        except (ValueError, AttributeError):
            output_device_uuid = None
    _output_ctx_token = OUTPUT_DEVICE_ID.set(output_device_uuid)

    # Debug shortcut: '/block <description>' bypasses the teacher entirely
    # and goes straight to the engineer. Kept for smoke testing the
    # frontend_engineer in isolation.
    trigger = _BLOCK_TRIGGER.match(question)
    if trigger:
        description = (trigger.group(1) or "").strip()
        return StreamingResponse(
            _block_trigger_stream(description, user_id),
            media_type="text/event-stream",
        )

    client = _get_client(request)

    # Resolve the active channel. We need it BEFORE assemble_context so
    # the right prompt builder (voice_answer vs answer) gets used.
    # talk_preference is fetched fresh by assemble_context anyway, so we
    # pre-fetch it here cheaply.
    device_class = (x_device_class or "").strip().lower()
    if device_class not in _VALID_DEVICE_CLASSES:
        device_class = "desktop"
    try:
        talk_pref = await client.get_talk_preference(user_id)
    except Exception as e:
        print(f"[ask/stream] talk_preference fetch failed: {e}", flush=True)
        talk_pref = None
    active_channel = _resolve_active_channel(talk_pref, device_class)
    voice_mode = active_channel in ("voice", "both")

    # Voice-leads two-call pattern (Phase 1). When the env flag is set and
    # the active channel is voice, the request runs as: (1) a tools-free
    # voice call that streams brief prose through auto-speak; (2) a
    # background canvas-writer call spawned after the voice `done` event,
    # which mounts at most one note derived from the voice transcript.
    # The non-voice path is unchanged; flag-off keeps the existing
    # single-turn voice path intact.
    voice_leads_enabled = voice_mode and os.environ.get("BWM_VOICE_LEADS", "0") == "1"

    req_id = getattr(request.state, "event_req_id", None)
    log_event(
        "ask.start",
        req_id=req_id,
        user_id=str(user_id),
        question_len=len(question),
        addressee=getattr(body, "addressee", None),
        device_class=device_class,
        active_channel=active_channel,
        voice_mode=voice_mode,
        voice_leads=voice_leads_enabled,
        x_lane_thinking=x_lane_thinking,
    )

    # Benchmark instrumentation: collect per-phase elapsed milliseconds keyed
    # off a single perf_counter origin. Reported on the terminal SSE event as
    # `phase_timings_ms`. No-op outside the benchmark path — instrumentation
    # cost is one perf_counter pair per step.
    timing_origin = time.perf_counter()
    phases: dict = {}
    phases["device_class"] = device_class
    phases["active_channel"] = active_channel
    phases["voice_mode"] = voice_mode

    # Lane A uses the "voice" profile (thinking on, reasoning_effort=high).
    # Brevity is enforced by the lane_a_voice skill prompt; thinking-mode
    # is now what keeps the reasoning hidden so the visible reply stays
    # short. Benchmarks can still flip thinking off via X-Lane-Thinking.
    thinking_override = (x_lane_thinking or "").strip().lower()
    lane_a_profile: Optional[str] = "voice"
    disable_thinking = False
    if thinking_override == "off":
        disable_thinking = True
        lane_a_profile = None
    elif thinking_override == "on":
        disable_thinking = False
    phases["disable_thinking"] = disable_thinking
    phases["profile"] = lane_a_profile or "(none)"
    phases["voice_leads"] = voice_leads_enabled

    with_assemble_t0 = time.perf_counter()
    ctx = await assemble_context(
        body, user_id, db, client,
        phases=phases, voice_mode=voice_mode, voice_leads=voice_leads_enabled,
    )
    phases["context_total_ms"] = round((time.perf_counter() - with_assemble_t0) * 1000, 2)
    # Voice-leads: no tools on the voice pass — auto-speak streams the
    # answer; the canvas-writer pass spawned after `done` handles visuals.
    teacher_tools = [] if voice_leads_enabled else build_teacher_tools(user_id)

    status_queue: asyncio.Queue = asyncio.Queue()

    async def run_generation():
        answer = ""
        answer_body = ""
        extracted_title: str | None = None
        usage: dict = {}
        # Auto-speak state. Only active on voice channels. The buffer
        # accumulates streamed prose; once a sentence terminator (.!?
        # followed by whitespace) is found, the sentence fires to Kokoro
        # in the background via tools.speak.speak(). If the LLM emits
        # its own `speak` tool call this turn, we shut off auto-speak to
        # avoid double-voicing the same content.
        sentence_buffer = ""
        auto_speak_suppressed = False
        auto_speak_count = 0
        auto_speak_first_ms: float | None = None

        async def _fire_sentence(sentence: str):
            """Background-fire a single sentence to TTS. Errors are
            logged and swallowed — auto-speak is a best-effort path."""
            nonlocal auto_speak_count, auto_speak_first_ms
            cleaned = _strip_for_speech(sentence)
            if not cleaned:
                return
            if auto_speak_first_ms is None:
                auto_speak_first_ms = round(
                    (time.perf_counter() - timing_origin) * 1000, 2
                )
                phases["auto_speak_first_ms"] = auto_speak_first_ms
            auto_speak_count += 1
            phases["auto_speak_count"] = auto_speak_count
            try:
                # channel='voice' on voice-only devices, 'both' on both.
                # Use the resolved active_channel directly — matches the
                # speak() tool's channel semantics.
                speak_channel = "voice" if active_channel == "voice" else "both"
                # Route to the requester's chosen output device when set.
                # Defaults to broadcasting (None) so existing single-device
                # behavior is unchanged.
                target_device = OUTPUT_DEVICE_ID.get()
                await tool_speak(
                    user_id=user_id,
                    text=cleaned,
                    channel=speak_channel,
                    target_device_id=target_device,
                )
            except Exception as e:
                print(f"[ask/stream] auto-speak failed: {e}", flush=True)

        try:
            title_resolved = False
            head_buffer = ""

            async for evt in run_teacher_tool_loop(
                static_system=ctx.parts.static_system,
                static_user_passage=ctx.parts.static_user_passage,
                dynamic_user=ctx.parts.dynamic_user,
                prior_messages=ctx.prior_messages,
                tools=teacher_tools,
                purpose="answer",
                user_id=user_id,
                phases=phases,
                timing_origin=timing_origin,
                # Lane A uses the "voice" profile (thinking on,
                # reasoning_effort=high). The X-Lane-Thinking=off override
                # falls back to plain disable_thinking=True.
                disable_thinking=disable_thinking,
                profile=lane_a_profile,
            ):
                if evt["kind"] == "delta":
                    chunk = evt["text"]
                    # Auto-speak: accumulate the streamed prose and fire
                    # each completed sentence to Kokoro as a background
                    # task. Voice-mode only; suppressed once the LLM
                    # calls speak() itself.
                    if voice_mode and not auto_speak_suppressed and chunk:
                        sentence_buffer += chunk
                        while True:
                            match = _SENTENCE_BOUNDARY.search(sentence_buffer)
                            if not match:
                                break
                            sentence = sentence_buffer[: match.end()].strip()
                            sentence_buffer = sentence_buffer[match.end():]
                            if sentence:
                                task = asyncio.create_task(_fire_sentence(sentence))
                                _background_tasks.add(task)
                                task.add_done_callback(_background_tasks.discard)
                    if not title_resolved:
                        head_buffer += chunk
                        # Only attempt parse_title once we've seen a newline.
                        # parse_title's regex matches against `$` (end-of-string)
                        # as well as `\n+`, which means partial mid-stream
                        # buffers like "TITLE: Dec" wrongly succeed before
                        # "oder Stacks" arrives. Wait for the newline boundary.
                        if "\n" in head_buffer:
                            title, body_text = parse_title(head_buffer)
                            if title is not None:
                                extracted_title = title
                                await status_queue.put({"type": "title", "title": title})
                                title_resolved = True
                                if body_text:
                                    await status_queue.put({"type": "token", "text": body_text})
                            elif not head_buffer.lstrip().upper().startswith("TITLE:"):
                                # Model didn't honor the format — flush as-is.
                                await status_queue.put({"type": "token", "text": head_buffer})
                                title_resolved = True
                    else:
                        await status_queue.put({"type": "token", "text": chunk})
                elif evt["kind"] == "tool_call":
                    # If the LLM is going to speak() itself, stop the
                    # auto-speak path — its text wins. Drop any pending
                    # buffer so we don't double-voice a partial sentence.
                    if voice_mode and evt.get("name") == "speak":
                        auto_speak_suppressed = True
                        sentence_buffer = ""
                    # Forward every tool call to the SSE stream so the
                    # frontend / benchmark can observe what the persona
                    # is invoking. Frontend ignores types it doesn't
                    # know; benchmark grades on these.
                    await status_queue.put({
                        "type": "tool_call",
                        "name": evt.get("name"),
                        "arguments": evt.get("arguments") or {},
                    })
                elif evt["kind"] == "done":
                    answer = evt["text"]
                    usage = evt["usage"]
                    if not title_resolved and head_buffer:
                        title, body_text = parse_title(head_buffer)
                        if title is not None:
                            extracted_title = title
                            await status_queue.put({"type": "title", "title": title})
                            if body_text:
                                await status_queue.put({"type": "token", "text": body_text})
                        else:
                            await status_queue.put({"type": "token", "text": head_buffer})
                        title_resolved = True

            # Flush any trailing prose that didn't end in a sentence
            # terminator — the user's last word still needs to be heard.
            if voice_mode and not auto_speak_suppressed:
                tail = sentence_buffer.strip()
                if tail:
                    task = asyncio.create_task(_fire_sentence(tail))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
                    sentence_buffer = ""

            final_title, answer_body = parse_title(answer)
            if extracted_title is None:
                extracted_title = final_title
            if not answer_body:
                answer_body = answer

            print(
                f"[ask/stream] answer length={len(answer_body)}, "
                f"title={extracted_title!r}, usage={usage}, first 100={answer_body[:100]!r}",
                flush=True,
            )
            await status_queue.put({
                "type": "debug",
                "static_system": ctx.parts.static_system,
                "static_user_passage": ctx.parts.static_user_passage,
                "dynamic_user": ctx.parts.dynamic_user,
                "prior_message_count": len(ctx.prior_messages),
                "usage": usage,
            })
            phases["total_ms"] = round((time.perf_counter() - timing_origin) * 1000, 2)
            log_event(
                "ask.done",
                req_id=req_id,
                user_id=str(user_id),
                answer_len=len(answer_body),
                title=extracted_title,
                usage=usage,
                phases=phases,
            )
            await status_queue.put({
                "type": "answer",
                "answer": answer_body,
                "title": extracted_title,
                "related_interaction_ids": [],
                "phase_timings_ms": phases,
            })

            # Phase 1 voice-leads: spawn the canvas-writer pass now that
            # the spoken answer is complete. The task runs detached from
            # the SSE stream — by the time the writer's tool call fires,
            # the user is already listening to the auto-spoken response,
            # and the note pops onto the canvas during playback.
            if voice_leads_enabled and answer_body:
                writer_task = asyncio.create_task(run_canvas_writer(
                    question=question,
                    transcript=answer_body,
                    user_id=user_id,
                    req_id=req_id,
                    origin=timing_origin,
                    source="ask",
                ))
                _background_tasks.add(writer_task)
                writer_task.add_done_callback(_background_tasks.discard)
                log_event(
                    "ask.voice_done",
                    req_id=req_id,
                    user_id=str(user_id),
                    transcript_len=len(answer_body),
                    auto_speak_first_ms=phases.get("auto_speak_first_ms"),
                )
        except Exception as e:
            print(f"[ask/stream] error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            await status_queue.put({"type": "error", "message": str(e)})

        try:
            async with async_session() as bg_db:
                interaction = Interaction(
                    user_id=user_id,
                    session_id=body.session_id,
                    parent_interaction_id=body.parent_interaction_id,
                    title=extracted_title,
                    passage_text=body.passage_text,
                    question=body.question,
                    answer=answer_body or answer,
                    source_document=str(body.document_id) if body.document_id else None,
                )
                bg_db.add(interaction)
                await bg_db.commit()
                await bg_db.refresh(interaction)

                await status_queue.put({
                    "type": "interaction",
                    "interaction_id": str(interaction.id),
                })

                print(f"[ask/stream] scheduling background task for {interaction.id}", flush=True)
                task = asyncio.get_event_loop().create_task(
                    post_interaction_update(interaction.id, user_id)
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
                print(f"[ask/stream] background task scheduled", flush=True)
        except Exception as e:
            print(f"[ask/stream] store error: {e}", flush=True)
            import traceback
            traceback.print_exc()

        await status_queue.put(None)

    async def event_stream():
        task = asyncio.create_task(run_generation())
        while True:
            msg = await status_queue.get()
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    client = _get_client(request)
    ctx = await assemble_context(body, user_id, db, client)
    answer, _ = await generate_cached(
        ctx.parts.static_system,
        ctx.parts.static_user_passage,
        ctx.parts.dynamic_user,
        prior_messages=ctx.prior_messages,
        profile="voice",
    )
    title, _ = parse_title(answer)

    interaction = Interaction(
        user_id=user_id,
        session_id=body.session_id,
        parent_interaction_id=body.parent_interaction_id,
        title=title,
        passage_text=body.passage_text,
        question=body.question,
        answer=answer,
        source_document=str(body.document_id) if body.document_id else None,
    )
    db.add(interaction)
    await db.commit()
    await db.refresh(interaction)

    background_tasks.add_task(post_interaction_update, interaction.id, user_id)

    return AskResponse(
        interaction_id=interaction.id,
        answer=answer,
        session_id=body.session_id,
        title=title,
        related_interaction_ids=[],
    )
