import asyncio
import json
import os
import time
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from infra.db import get_db, async_session
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
from persona.teacher.tools.grants import TEACHER_GRANT
from infra.contracts.output_routing import OUTPUT_DEVICE_ID
from persona.teacher.tools import request_session_control as _request_session_control
from services.persona.routers._ask_addressee import route_addressee
from services.persona.routers._ask_session import run_session_control
from services.persona.routers._ask_voice import (
    AutoSpeakBuffer,
    normalize_device_class,
    resolve_active_channel,
)

router = APIRouter()

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()


def _get_client(request: Request) -> SiliconBrainClient:
    client = getattr(request.app.state, "brain_client", None)
    if client is None:
        client = SiliconBrainClient()
        request.app.state.brain_client = client
    return client


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

    # Non-teacher addressees (`/block` engineer shortcut, app_operator persona)
    # are early returns — not teacher Q&A turns — so they skip engagement
    # emission, channel resolution, and context assembly.
    early = route_addressee(question, body, user_id)
    if early is not None:
        return early

    client = _get_client(request)

    # Resolve the active channel. We need it BEFORE assemble_context so
    # the right prompt builder (voice_answer vs answer) gets used.
    # talk_preference is fetched fresh by assemble_context anyway, so we
    # pre-fetch it here cheaply.
    device_class = normalize_device_class(x_device_class)
    try:
        talk_pref = await client.get_talk_preference(user_id)
    except Exception as e:
        print(f"[ask/stream] talk_preference fetch failed: {e}", flush=True)
        talk_pref = None
    active_channel = resolve_active_channel(talk_pref, device_class)
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

    # Engagement boundary + signal.turn_arrived emission (PR-3). Runs
    # AFTER the addressee early-returns (a `/block` invocation isn't a real
    # teacher turn) and BEFORE assemble_context so the
    # current_engagement_state projection is fresh when the prompt builder
    # reads it. Failures degrade silently — observability shouldn't break
    # the user-facing turn.
    try:
        from persona.teacher.engagement import ensure_engagement_and_emit_turn
        await ensure_engagement_and_emit_turn(user_id, source="ask")
    except Exception as e:
        print(f"[ask/stream] engagement emission failed: {e}", flush=True)

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
    # Voice-leads: no teaching tools on the voice pass — auto-speak streams
    # the answer; the canvas-writer pass spawned after `done` handles visuals.
    # Both paths get the Stage-1 routing tool: the fast-line LLM decides (with
    # the teacher/session_routing guidance) whether the turn is out of the
    # teaching loop and, if so, calls request_session_control to hand off to
    # Stage-2 session control. Voice-leads Pass 1 stays otherwise tool-free.
    routing_tool = _request_session_control.build_spec(user_id)
    teacher_tools = (
        [routing_tool] if voice_leads_enabled
        else build_teacher_tools(user_id) + [routing_tool]
    )

    status_queue: asyncio.Queue = asyncio.Queue()

    async def run_generation():
        answer = ""
        answer_body = ""
        extracted_title: str | None = None
        usage: dict = {}
        session_routed = False
        # Auto-speak: only active on voice channels. The buffer accumulates
        # streamed prose and fires each completed sentence to Kokoro in the
        # background; if the LLM emits its own `speak` tool call we suppress
        # it to avoid double-voicing the same content.
        autospeak = AutoSpeakBuffer(
            user_id=user_id,
            active_channel=active_channel,
            timing_origin=timing_origin,
            phases=phases,
            background_tasks=_background_tasks,
        )

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
                terminal_tools={_request_session_control.NAME},
                grant=TEACHER_GRANT,
            ):
                if evt["kind"] == "delta":
                    chunk = evt["text"]
                    # Auto-speak: voice-mode only; fires completed sentences
                    # as background tasks.
                    if voice_mode:
                        autospeak.feed(chunk)
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
                    # Stage-1 hand-off: the model judged this turn to be
                    # outside the teaching loop. Suppress the spoken reply +
                    # the canvas draw; Stage-2 session control takes over.
                    if evt.get("name") == _request_session_control.NAME:
                        session_routed = True
                        if voice_mode:
                            autospeak.suppress()
                        continue
                    # If the LLM is going to speak() itself, stop the
                    # auto-speak path — its text wins.
                    if voice_mode and evt.get("name") == "speak":
                        autospeak.suppress()
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

            if session_routed:
                # Stage-1 routed out of the teaching loop. Run Stage-2 session
                # control — the model picks a session tool (end_session). No
                # spoken reply, no canvas draw, no Q&A Interaction.
                async for sse in run_session_control(question, user_id, body):
                    await status_queue.put(sse)
                phases["total_ms"] = round((time.perf_counter() - timing_origin) * 1000, 2)
                log_event("ask.session_routed", req_id=req_id, user_id=str(user_id))
            else:
                # Flush any trailing prose that didn't end in a sentence
                # terminator — the user's last word still needs to be heard.
                if voice_mode:
                    autospeak.flush_tail()

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

        # A session action (Stage-2 routed) is not a Q&A turn — store nothing.
        if not session_routed:
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
