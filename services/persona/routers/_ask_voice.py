"""Voice / auto-speak helpers for `/api/ask/stream` — channel resolution and the
auto-speak sentence buffer. Extracted from `ask.py` (F6); behavior is verbatim.

`AutoSpeakBuffer` encapsulates the streaming auto-speak state machine that used
to live inline in `run_generation`: accumulate streamed prose, fire each
completed sentence to TTS in the background, suppress once the LLM calls
`speak()` itself, and flush the trailing fragment at stream close.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional
from uuid import UUID

from infra.contracts.output_routing import OUTPUT_DEVICE_ID
from tools.speak import speak as tool_speak


# Sentence terminator detector for auto-speak. Mirrors
# `services/speak/main.py:_SENTENCE_SPLIT` so the boundary the client would
# split on matches what we fire here. We additionally accept end-of-string
# when flushing the buffer at stream close.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)")

_VALID_DEVICE_CLASSES = {"desktop", "tablet", "phone"}


def normalize_device_class(raw: str | None) -> str:
    """Validate the `X-Device-Class` header → one of desktop|tablet|phone
    (default desktop)."""
    dc = (raw or "").strip().lower()
    return dc if dc in _VALID_DEVICE_CLASSES else "desktop"


def resolve_active_channel(talk_preference: dict | None, device_class: str) -> str:
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


class AutoSpeakBuffer:
    """Streaming auto-speak buffer. Active only on voice channels; the caller
    gates `feed`/`suppress`/`flush_tail` on `voice_mode`.

    Accumulates streamed prose; once a sentence terminator (`.!?` + whitespace)
    is found, the sentence fires to Kokoro in the background via `tools.speak`.
    If the LLM emits its own `speak` tool call, `suppress()` shuts auto-speak off
    so the same content isn't double-voiced. Fired tasks are registered in the
    caller's `background_tasks` set so they survive GC. Latency is recorded into
    `phases` (`auto_speak_first_ms`, `auto_speak_count`) exactly as before.
    """

    def __init__(
        self, *,
        user_id: UUID,
        active_channel: str,
        timing_origin: float,
        phases: dict,
        background_tasks: set,
    ):
        self._user_id = user_id
        self._active_channel = active_channel
        self._timing_origin = timing_origin
        self._phases = phases
        self._bg = background_tasks
        self._buffer = ""
        self._suppressed = False
        self._count = 0
        self._first_ms: float | None = None

    @property
    def suppressed(self) -> bool:
        return self._suppressed

    async def _fire(self, sentence: str) -> None:
        """Background-fire a single sentence to TTS. Errors are logged and
        swallowed — auto-speak is a best-effort path."""
        cleaned = _strip_for_speech(sentence)
        if not cleaned:
            return
        if self._first_ms is None:
            self._first_ms = round((time.perf_counter() - self._timing_origin) * 1000, 2)
            self._phases["auto_speak_first_ms"] = self._first_ms
        self._count += 1
        self._phases["auto_speak_count"] = self._count
        try:
            # channel='voice' on voice-only devices, 'both' on both. Use the
            # resolved active_channel directly — matches speak()'s semantics.
            speak_channel = "voice" if self._active_channel == "voice" else "both"
            # Route to the requester's chosen output device when set; defaults
            # to broadcasting (None) so existing single-device behavior is kept.
            target_device = OUTPUT_DEVICE_ID.get()
            await tool_speak(
                user_id=self._user_id,
                text=cleaned,
                channel=speak_channel,
                target_device_id=target_device,
            )
        except Exception as e:
            print(f"[ask/stream] auto-speak failed: {e}", flush=True)

    def _spawn(self, sentence: str) -> None:
        task = asyncio.create_task(self._fire(sentence))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def feed(self, chunk: str) -> None:
        """Accumulate a streamed chunk; background-fire each completed sentence."""
        if self._suppressed or not chunk:
            return
        self._buffer += chunk
        while True:
            match = _SENTENCE_BOUNDARY.search(self._buffer)
            if not match:
                break
            sentence = self._buffer[: match.end()].strip()
            self._buffer = self._buffer[match.end():]
            if sentence:
                self._spawn(sentence)

    def suppress(self) -> None:
        """The LLM called speak() itself — stop auto-speak, drop the partial
        buffer so we don't double-voice a partial sentence."""
        self._suppressed = True
        self._buffer = ""

    def flush_tail(self) -> None:
        """Fire any trailing prose that didn't end in a sentence terminator —
        the user's last word still needs to be heard."""
        if self._suppressed:
            return
        tail = self._buffer.strip()
        if tail:
            self._spawn(tail)
            self._buffer = ""
