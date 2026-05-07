"""Deterministic rendering of the TALK CHANNEL RULE in the system prompt.

The teacher's prompt block must render the user's per-device preference
as an unambiguous rule the LLM can follow without judgment.
"""
from __future__ import annotations

from persona.teacher.prompts.preferences_block import render_talk_preference


def _joined(prefs):
    return "\n".join(render_talk_preference(prefs))


def test_render_uses_defaults_when_pref_is_none():
    body = _joined(None)
    assert "TALK CHANNEL RULE" in body
    assert "desktop → channel='both'" in body
    assert "tablet  → channel='both'" in body
    assert "phone   → channel='text'" in body


def test_render_reflects_user_choice():
    body = _joined({"desktop": "voice", "tablet": "text", "phone": "both"})
    assert "desktop → channel='voice'" in body
    assert "tablet  → channel='text'" in body
    assert "phone   → channel='both'" in body


def test_render_falls_back_for_partial_or_invalid():
    body = _joined({"desktop": "voice", "phone": "shout"})
    assert "desktop → channel='voice'" in body
    # Missing → default
    assert "tablet  → channel='both'" in body
    # Invalid → default
    assert "phone   → channel='text'" in body


def test_render_includes_channel_required_reminder():
    body = _joined(None)
    assert "channel" in body and "required" in body.lower()
