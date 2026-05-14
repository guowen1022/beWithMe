"""Canvas-writer pass — voice-leads Phase 1.

Runs *after* the spoken pass of a voice turn completes. Given the user's
question (or trigger summary) and the spoken transcript, builds a small
prompt exposing only `mount_template` and runs one short tool loop.

Detached from the SSE stream of the originating request: callers spawn
this as `asyncio.create_task(run_canvas_writer(...))` and forget. The
writer's mount fires onto the user's canvas via the same SSE channel
the existing mount_template tool uses.

Shared between the typed path (`services/persona/routers/ask.py`) and
the perception/Lane A path (`persona/teacher/triggers.py`).
"""
from __future__ import annotations

import time
import traceback
from typing import Optional
from uuid import UUID

from infra.event_log import log_event
from persona.teacher.prompts.canvas_writer import build as build_canvas_writer_prompt
from persona.teacher.tools.loop import run as run_teacher_tool_loop
from persona.teacher.tools.manifest import build_tools
from workshop.canvas.tools.read_media import read_media


async def run_canvas_writer(
    *,
    question: str,
    transcript: str,
    user_id: UUID,
    req_id: Optional[str] = None,
    origin: Optional[float] = None,
    source: str = "ask",
) -> None:
    """Voice-leads canvas pass — runs after the spoken answer completes.

    Builds the writer-only prompt (`prompts/canvas_writer.py`) which
    exposes just `mount_template`. The model decides whether to mount a
    rich_card or stay silent. Errors are caught and logged; we never
    raise — the SSE stream that birthed this task is already closed.
    """
    writer_t0 = time.perf_counter()
    canvas_state = None
    try:
        canvas_state = await read_media(user_id)
    except Exception as e:
        print(f"[writer] read_media error: {e}", flush=True)

    parts = build_canvas_writer_prompt(
        question=question,
        voice_transcript=transcript,
        canvas_state=canvas_state,
    )
    writer_tools = build_tools(user_id, lane="writer")

    mount_fired = False
    error: Optional[str] = None
    try:
        async for evt in run_teacher_tool_loop(
            static_system=parts.static_system,
            static_user_passage=parts.static_user_passage,
            dynamic_user=parts.dynamic_user,
            prior_messages=None,
            tools=writer_tools,
            purpose="canvas-writer",
            user_id=user_id,
            max_iterations=2,
            profile="voice",
        ):
            if evt.get("kind") == "tool_call" and evt.get("name") == "mount_template":
                mount_fired = True
    except Exception as e:
        error = str(e)
        print(f"[writer] tool loop error: {e}", flush=True)
        traceback.print_exc()

    log_event(
        "ask.writer_done",
        req_id=req_id,
        user_id=str(user_id),
        source=source,
        wall_ms=round((time.perf_counter() - writer_t0) * 1000, 2),
        since_request_ms=(
            round((time.perf_counter() - origin) * 1000, 2)
            if origin is not None
            else None
        ),
        mount_fired=mount_fired,
        transcript_len=len(transcript),
        error=error,
    )


__all__ = ["run_canvas_writer"]
