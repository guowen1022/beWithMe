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

import json
import time
from typing import Any, Dict, Optional
from uuid import UUID

from infra import perception
from infra.contracts.ui import BlockMessage, VoicePlay
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from infra.silicon_brain_client import SiliconBrainClient

from infra.model.tools import ToolSpec


# Logical "block id" we tag the BlockMessage with. The frontend overlay
# filters on the topic, not the block id, but we keep this stable so the
# event is well-formed and trace logs read sensibly.
_TEACHER_SPEECH_BLOCK_ID = "teacher-speech"
_TEACHER_SPEECH_TOPIC = "teacher-speech.text"


async def _load_voice_prefs(user_id: UUID) -> tuple[str, float, str]:
    """Fetch (voice_id, voice_speed, voice_lang) via the knowledge sidecar.

    The kokoro-matching defaults are applied server-side by
    `get_or_create_preferences`, so the row always exists by read time.
    """
    client = SiliconBrainClient()
    try:
        prefs = await client.get_voice_preferences(user_id)
    finally:
        await client.aclose()
    return prefs["voice_id"], float(prefs["voice_speed"]), prefs["voice_lang"]


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
        source="teacher",
    )

    return {
        "voice_delivered": voice_delivered,
        "caption_delivered": caption_delivered,
    }


__all__ = ["speak", "build_spec"]

def _make_speak(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        text = (args.get("text") or "").strip()
        if not text:
            return json.dumps({"error": "text is required"})
        channel = (args.get("channel") or "").strip()
        if channel not in ("voice", "text", "both"):
            return json.dumps({
                "error": "channel must be 'voice', 'text', or 'both'"
            })
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        # Cross-device output routing: default to request's X-Output-Device-Id
        # when persona didn't pick a target.
        if target_uuid is None:
            from infra.contracts.output_routing import get_output_device_id
            ctx_target = get_output_device_id()
            if ctx_target is not None:
                target_uuid = ctx_target
        try:
            delivered = await speak(
                user_id=user_id,
                text=text,
                channel=channel,
                voice=args.get("voice"),
                speed=args.get("speed"),
                lang=args.get("lang"),
                target_device_id=target_uuid,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(delivered)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="speak",
        description=(
            "Deliver an utterance to the user via voice (Kokoro audio), "
            "an on-screen caption (a borderless, always-on-top floating "
            "strip near the bottom of the screen, like YouTube CC, that "
            "reveals left-to-right at reading speed and auto-fades), or "
            "both. Pick `channel` based on TALK PREFERENCE in the "
            "system context plus the active device class (see "
            "CURRENTLY ON CANVAS). Voice / speed / lang default to the "
            "user's saved preferences; override only if the user is "
            "explicit."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "What to say. 1-3 sentences works best for both audio latency and an on-screen line that is readable at a glance.",
                },
                "channel": {
                    "type": "string",
                    "enum": ["voice", "text", "both"],
                    "description": "How to deliver this utterance. 'voice' plays audio only; 'text' shows it in the teacher-speech block only; 'both' does both.",
                },
                "voice": {
                    "type": "string",
                    "description": "Optional kokoro voice id (e.g., 'af_heart'). Only used when channel includes voice.",
                },
                "speed": {
                    "type": "number",
                    "description": "Optional 0.5-2.0 multiplier on speaking rate. Only used when channel includes voice.",
                },
                "lang": {
                    "type": "string",
                    "description": "Optional language tag (e.g., 'en-us'). Only used when channel includes voice.",
                },
                "target_device_id": {"type": "string"},
            },
            "required": ["text", "channel"],
            "additionalProperties": False,
        },
        executor=_make_speak(user_id),
    )
