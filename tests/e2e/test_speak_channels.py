"""Channel branches on `tools.speak.speak`.

The speak tool supports `channel` ∈ {"voice", "text", "both"}:

  * voice → emits one VoicePlay SSE event (audio).
  * text  → emits one BlockMessage on `teacher-speech.text` (the
            global TeacherCaption overlay renders it). No mount —
            the caption is a fixed-position overlay component, not a
            grid block.
  * both  → emits both, in order: VoicePlay then BlockMessage.

These tests patch the dynamic-router enqueue and stub voice prefs so
neither side reaches a real SSE subscriber or the database.
"""
from __future__ import annotations

import asyncio
import uuid

from infra.contracts.ui import BlockMessage, VoicePlay
from infra.perception import cache as perc_cache
from tools import speak as speak_tool


def _patch(monkeypatch):
    captured: list = []

    async def fake_enqueue_for_user(user_id, event):
        captured.append(event)
        return 1

    async def fake_enqueue_for_device(user_id, device_id, event):
        captured.append(event)
        return 1

    async def fake_voice_prefs(user_id):
        return ("af_heart", 1.0, "en-us")

    monkeypatch.setattr(speak_tool, "enqueue_for_user", fake_enqueue_for_user)
    monkeypatch.setattr(speak_tool, "enqueue_for_device", fake_enqueue_for_device)
    monkeypatch.setattr(speak_tool, "_load_voice_prefs", fake_voice_prefs)
    return captured


def _kinds(events):
    return [type(e).__name__ for e in events]


def test_speak_channel_voice_emits_only_voice_play(monkeypatch):
    captured = _patch(monkeypatch)
    perc_cache._reset_for_tests()
    uid = uuid.uuid4()

    out = asyncio.run(speak_tool.speak(
        user_id=uid, text="hello there", channel="voice",
    ))

    assert _kinds(captured) == ["VoicePlay"]
    assert isinstance(captured[0], VoicePlay)
    assert captured[0].text == "hello there"
    assert out["voice_delivered"] == 1
    assert out["caption_delivered"] == 0
    # Utterance recorded so the teacher can read it back later.
    assert len(perc_cache.read_for_user(uid)["voice_log"]) == 1


def test_speak_channel_text_emits_only_block_message(monkeypatch):
    captured = _patch(monkeypatch)
    perc_cache._reset_for_tests()
    uid = uuid.uuid4()

    out = asyncio.run(speak_tool.speak(
        user_id=uid, text="visible line", channel="text",
    ))

    # Single BlockMessage on the caption topic. No UIUpdate, no VoicePlay.
    assert _kinds(captured) == ["BlockMessage"], captured
    msg = captured[0]
    assert isinstance(msg, BlockMessage)
    assert msg.block_id == "teacher-speech"
    assert msg.topic == "teacher-speech.text"
    assert msg.value["text"] == "visible line"
    assert out["voice_delivered"] == 0
    assert out["caption_delivered"] == 1


def test_speak_channel_text_repeated_calls_each_emit_one_message(monkeypatch):
    captured = _patch(monkeypatch)
    perc_cache._reset_for_tests()
    uid = uuid.uuid4()

    asyncio.run(speak_tool.speak(user_id=uid, text="line one", channel="text"))
    asyncio.run(speak_tool.speak(user_id=uid, text="line two", channel="text"))
    asyncio.run(speak_tool.speak(user_id=uid, text="line three", channel="text"))

    # Each call → one BlockMessage. The overlay handles its own dwell/fade,
    # so the backend doesn't need to dedupe or batch.
    assert _kinds(captured) == ["BlockMessage", "BlockMessage", "BlockMessage"]
    assert [m.value["text"] for m in captured] == ["line one", "line two", "line three"]


def test_speak_channel_both_emits_voice_then_caption(monkeypatch):
    captured = _patch(monkeypatch)
    perc_cache._reset_for_tests()
    uid = uuid.uuid4()

    asyncio.run(speak_tool.speak(
        user_id=uid, text="loud and visible", channel="both",
    ))

    # Voice first, then caption — order matters because the caption's
    # reveal animation is timed to start as audio begins.
    assert _kinds(captured) == ["VoicePlay", "BlockMessage"], captured
    voice, caption = captured
    assert voice.text == "loud and visible"
    assert caption.value["text"] == "loud and visible"


def test_speak_rejects_unknown_channel(monkeypatch):
    _patch(monkeypatch)
    perc_cache._reset_for_tests()
    uid = uuid.uuid4()

    try:
        asyncio.run(speak_tool.speak(user_id=uid, text="x", channel="shout"))
    except ValueError as e:
        assert "channel must be" in str(e)
        return
    raise AssertionError("expected ValueError for invalid channel")
