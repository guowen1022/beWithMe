"""POST /api/perception/utterance — ambient mic block → perception cache.

Receives the transcript of one user phrase from the ambient_mic block on
the canvas. Records the utterance into the in-memory perception cache,
which fires a UserSpeechEvent that each persona's trigger orchestrator
can subscribe to (filtered by `target_persona`).

No DB writes. Talk is cheap; the utterance lives only as long as the
persona sidecar process.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from infra.auth import parse_user_id as get_current_user_id
from infra.contracts.ui import TeacherThinking
from infra.event_log import log_event
from infra.perception import is_likely_echo, record_user_speech
from infra.devices.delivery import enqueue_for_user


router = APIRouter()


class UtteranceRequest(BaseModel):
    text: str = Field(..., min_length=1)
    language: Optional[str] = None
    audio_duration_s: Optional[float] = None
    # Routing key — which persona should react. Filtered server-side by
    # each persona's trigger orchestrator. Defaults to teacher (the only
    # persona today). Future personas plug in by registering their own
    # filter on this field.
    target_persona: str = "teacher"


class UtteranceResponse(BaseModel):
    # `accepted` is the canonical signal — True when the utterance was
    # recorded into the perception cache, False when it was dropped
    # (today only on echo dedup). `reason` is set when accepted=False
    # so the frontend can decide how to render the rejection (e.g.
    # "echo" → quietly clear the displayed transcript so the user
    # doesn't see their own speakers' words echo back). `recorded`
    # is the legacy field name; mirrors `accepted` for back-compat
    # and will be removed once no callers rely on it.
    accepted: bool = True
    reason: Optional[str] = None
    recorded: bool = True


@router.post("/perception/utterance", response_model=UtteranceResponse, status_code=202)
async def post_utterance(
    body: UtteranceRequest,
    user_id: UUID = Depends(get_current_user_id),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
) -> UtteranceResponse:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    device_id: Optional[UUID] = None
    if x_device_id:
        try:
            device_id = UUID(x_device_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-Device-Id not a valid UUID")

    log_event(
        "perception.utterance.received",
        user_id=str(user_id),
        text_len=len(text),
        language=body.language,
        audio_duration_s=body.audio_duration_s,
        target_persona=body.target_persona,
        device_id=str(device_id) if device_id else None,
    )

    # Echo dedup. The ambient_mic block on the same device as the
    # speakers can pick up the teacher's own TTS through the mic.
    # The transcript that comes back is essentially what the teacher
    # just said. Drop it server-side instead of in the frontend so
    # the user can still INTERRUPT the teacher with anything that
    # isn't a near-match of the running utterance ("stop", "wait",
    # a different question).
    if is_likely_echo(user_id, text):
        print(
            f"[perception_utterance] dropped echo: user={user_id} text={text[:80]!r}",
            flush=True,
        )
        log_event(
            "perception.utterance.dropped_echo",
            user_id=str(user_id),
            text_len=len(text),
        )
        return UtteranceResponse(accepted=False, reason="echo", recorded=False)

    record_user_speech(
        user_id=user_id,
        text=text,
        language=body.language,
        audio_duration_s=body.audio_duration_s,
        target_persona=body.target_persona,
        device_id=device_id,
    )
    log_event(
        "perception.utterance.recorded",
        user_id=str(user_id),
        text_len=len(text),
        target_persona=body.target_persona,
    )

    # Surface every heard phrase in the existing teacher-thinking debug
    # panel. Decoupled from the trigger filter — even if the targeted
    # persona stays silent (or the event is filtered out for not being
    # this persona's), the user still gets visible confirmation that the
    # mic + transcribe path works end-to-end.
    short = text if len(text) <= 200 else (text[:197] + "…")
    summary_bits = [f"→ {body.target_persona}"]
    if body.language:
        summary_bits.append(f"[{body.language}]")
    if body.audio_duration_s:
        summary_bits.append(f"{body.audio_duration_s:.1f}s")
    try:
        await enqueue_for_user(user_id, TeacherThinking(
            phase="end",
            trigger="ambient-mic",
            summary=" ".join(summary_bits),
            text=short,
        ))
    except Exception as e:
        print(f"[perception_utterance] debug enqueue failed: {e}", flush=True)

    return UtteranceResponse()
