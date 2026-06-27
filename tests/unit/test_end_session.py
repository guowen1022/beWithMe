"""Unit tests for the `end_session` tool — the single code block that ends the
session AND navigates home.

Detection (does the user want to end / go home?) is the LLM's job. Execution is
this tool: two function calls in code — save the session, then `go_home`. The
`go_home` call is unconditional, so the user always lands on the feed even when
there's nothing to save or the save fails. See the "go home shows the feed" fix.
"""
import asyncio
import json
from unittest.mock import MagicMock
from uuid import uuid4

from persona.teacher.tools import end_session


def _run_executor(monkeypatch, *, session_id=None, save_raises=False):
    captured = []

    async def _enqueue(user_id, action):
        captured.append(action)
        return 1

    monkeypatch.setattr(end_session, "enqueue_for_user", _enqueue)
    if save_raises:
        monkeypatch.setattr(end_session.httpx, "AsyncClient", MagicMock(side_effect=RuntimeError("boom")))

    spec = end_session.build_spec(uuid4(), session_id)
    out = json.loads(asyncio.run(spec.executor({})))
    return captured, out


def test_go_home_fires_on_voice_turn_with_no_session(monkeypatch):
    # Voice turns carry no session_id: nothing to save, but still navigate home.
    captured, out = _run_executor(monkeypatch, session_id=None)
    assert [a.action for a in captured] == ["go_home"]
    assert out["ok"] is True and out["saved"] is False


def test_go_home_fires_even_when_save_fails(monkeypatch):
    # Save is best-effort and must never block navigation.
    captured, out = _run_executor(monkeypatch, session_id=uuid4(), save_raises=True)
    assert [a.action for a in captured] == ["go_home"]
    assert out["saved"] is False


def test_description_covers_go_home_phrasings(monkeypatch):
    # Detection guidance for the LLM: end_session is the tool for "go home" too.
    spec = end_session.build_spec(uuid4())
    desc = spec.description.lower()
    assert "go home" in desc and "back to the feed" in desc
