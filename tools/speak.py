"""speak — teacher's tool for delivering an utterance via voice, an
on-screen caption, or both.

Channels:
  * `voice`: emit a `VoicePlay` SSE event. The frontend's `SpeakerSink`
    fetches `/api/speak/stream` and pipes the synthesised PCM into Web
    Audio. SSE itself is text-only — per-device fetch handles audio
    backpressure naturally.
  * `text`: emit a `BlockMessage` on `teacher-speech.text`. The
    frontend's global `TeacherCaption` overlay (mounted in
    `app/layout.tsx`) catches it and renders the line as a floating,
    borderless, always-on-top caption — YouTube-CC style — with a
    left-to-right reveal aligned to reading speed. No grid block, no
    mounting; the caption auto-fades after dwell.
  * `both`: do both at once. Single tool call so the visible and
    audible sides cannot drift.

Voice / speed / lang fall back to the user's `UserPreferences` row.
The LLM picks `channel` per call based on the user's stored
`talk_preference` (plain English, often device-conditional) and the
active device class — both injected into the system prompt.
"""
from __future__ import annotations

import time
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from infra import perception
from infra.contracts.ui import BlockMessage, VoicePlay
from infra.db import async_session
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user
from silicon_brain.models.user_preferences import (
    DEFAULT_VOICE_ID,
    DEFAULT_VOICE_LANG,
    DEFAULT_VOICE_SPEED,
    UserPreferences,
)


# Logical "block id" we tag the BlockMessage with. The frontend overlay
# filters on the topic, not the block id, but we keep this stable so the
# event is well-formed and trace logs read sensibly.
_TEACHER_SPEECH_BLOCK_ID = "teacher-speech"
_TEACHER_SPEECH_TOPIC = "teacher-speech.text"


async def _load_voice_prefs(user_id: UUID) -> tuple[str, float, str]:
    """Fetch (voice_id, voice_speed, voice_lang) for the user.

    Falls through to the kokoro-matching defaults if the row doesn't
    exist yet.
    """
    async with async_session() as session:
        result = await session.execute(
            select(UserPreferences).where(UserPreferences.user_id == user_id)
        )
        row = result.scalar_one_or_none()
    if row is None:
        return DEFAULT_VOICE_ID, DEFAULT_VOICE_SPEED, DEFAULT_VOICE_LANG
    return row.voice_id, float(row.voice_speed), row.voice_lang


async def _send(user_id: UUID, target_device_id: Optional[UUID], event) -> int:
    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


async def speak(
    *,
    user_id: UUID,
    text: str,
    channel: str = "voice",
    voice: Optional[str] = None,
    speed: Optional[float] = None,
    lang: Optional[str] = None,
    target_device_id: Optional[UUID] = None,
) -> dict:
    """Deliver `text` to the user.

    `channel` ∈ `{"voice", "text", "both"}`:
      * voice → audio only
      * text  → on-screen caption only (TeacherCaption overlay)
      * both  → audio + caption in the same call

    Returns `{"voice_delivered": int, "caption_delivered": int}`.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    if channel not in ("voice", "text", "both"):
        raise ValueError(
            f"channel must be 'voice', 'text', or 'both' (got {channel!r})"
        )

    voice_delivered = 0
    caption_delivered = 0
    chosen_voice: Optional[str] = None

    if channel in ("voice", "both"):
        pref_voice, pref_speed, pref_lang = await _load_voice_prefs(user_id)
        chosen_voice = voice or pref_voice
        event = VoicePlay(
            text=text,
            voice=chosen_voice,
            speed=speed if speed is not None else pref_speed,
            lang=lang or pref_lang,
        )
        voice_delivered = await _send(user_id, target_device_id, event)

    if channel in ("text", "both"):
        caption = BlockMessage(
            block_id=_TEACHER_SPEECH_BLOCK_ID,
            topic=_TEACHER_SPEECH_TOPIC,
            value={"text": text, "ts": time.time()},
        )
        caption_delivered = await _send(user_id, target_device_id, caption)

    # Record the utterance regardless of channel so the teacher can
    # read what it has already said (via `read_media`) and avoid
    # repeating itself, no matter how the user was actually addressed.
    perception.record_voice(
        user_id=user_id,
        text=text,
        voice=chosen_voice,
        device_id=target_device_id,
    )

    return {
        "voice_delivered": voice_delivered,
        "caption_delivered": caption_delivered,
    }


__all__ = ["speak"]
