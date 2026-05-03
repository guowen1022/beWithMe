"""speak — teacher's tool for synthesizing voice on the user's speakers.

Backend behavior: emit a `VoicePlay` SSE event on the user's dynamic
channel. The frontend's SpeakerSink picks it up and fetches
`/api/speak/stream` itself, piping the resulting PCM into Web Audio.
We deliberately don't synthesize and stream audio bytes through SSE —
SSE is text-only and per-device fetch handles backpressure naturally.

Voice / speed / lang default to the user's UserPreferences row when not
explicitly overridden by the teacher's tool call. The defaults on that
row match kokoro's defaults, so a fresh user with no settings still
produces sound.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select

from infra.contracts.ui import VoicePlay
from infra.db import async_session
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user
from silicon_brain.models.user_preferences import (
    DEFAULT_VOICE_ID,
    DEFAULT_VOICE_LANG,
    DEFAULT_VOICE_SPEED,
    UserPreferences,
)


async def _load_voice_prefs(user_id: UUID) -> tuple[str, float, str]:
    """Fetch (voice_id, voice_speed, voice_lang) for the user.

    Falls through to the kokoro-matching defaults if the row doesn't
    exist yet (no PUT /api/preferences ever issued).
    """
    async with async_session() as session:
        result = await session.execute(
            select(UserPreferences).where(UserPreferences.user_id == user_id)
        )
        row = result.scalar_one_or_none()
    if row is None:
        return DEFAULT_VOICE_ID, DEFAULT_VOICE_SPEED, DEFAULT_VOICE_LANG
    return row.voice_id, float(row.voice_speed), row.voice_lang


async def speak(
    *,
    user_id: UUID,
    text: str,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
    lang: Optional[str] = None,
    target_device_id: Optional[UUID] = None,
) -> int:
    """Send a VoicePlay event. Returns the number of SSE queues it landed in."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")

    pref_voice, pref_speed, pref_lang = await _load_voice_prefs(user_id)
    event = VoicePlay(
        text=text,
        voice=voice or pref_voice,
        speed=speed if speed is not None else pref_speed,
        lang=lang or pref_lang,
    )

    if target_device_id is not None:
        return await enqueue_for_device(user_id, target_device_id, event)
    return await enqueue_for_user(user_id, event)


__all__ = ["speak"]
