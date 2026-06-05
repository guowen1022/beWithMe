"""E2E for engagement-boundary detection + signal emission (PR-3).

Two surfaces under test:
  1. The helper `persona.teacher.engagement.ensure_engagement_and_emit_turn`
     drives the state machine deterministically when called in-process
     with explicit `now=` timestamps. SiliconBrainClient inside the helper
     is redirected at the test sidecar via KNOWLEDGE_SERVICE_URL.
  2. The HTTP path: `/api/ask/stream` and (indirectly) the voice trigger
     fire the helper as a side effect of a real turn.

Plus the `engagement_log` view and a forbidden-metrics sweep across the
test user's event stream.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest


# Keys the SPEC §17 forbids as event-body fields. The Maestro's reward
# signal must never become "how long did the user stay on screen" or
# "how many turns did we squeeze out of them" — those are dark-pattern
# optimisation targets. The sweep below asserts no event body carries
# any of these.
_FORBIDDEN_BODY_KEYS = {
    "session_length", "session_duration", "session_duration_s",
    "session_duration_ms", "time_in_app", "time_in_session",
    "time_on_screen", "turn_count", "turns_per_session",
    "engagement_length", "engagement_duration", "engagement_duration_s",
    "engagement_duration_ms",
}


@pytest.fixture(scope="module")
def knowledge_url_env(services):
    """Point SiliconBrainClient (used by the engagement helper) at the
    test sidecar by setting KNOWLEDGE_SERVICE_URL.

    Module-scoped restore so we don't leak the override into other modules
    in the same session that might assume the default localhost:8002.
    """
    prior = os.environ.get("KNOWLEDGE_SERVICE_URL")
    os.environ["KNOWLEDGE_SERVICE_URL"] = services["knowledge_url"]
    yield
    if prior is None:
        os.environ.pop("KNOWLEDGE_SERVICE_URL", None)
    else:
        os.environ["KNOWLEDGE_SERVICE_URL"] = prior


def _fresh_user(http: httpx.Client) -> tuple[str, dict]:
    tag = int(time.time() * 1000)
    resp = http.post("/api/users", json={"username": f"e2e-engagement-{tag}-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    uid = resp.json()["id"]
    return uid, {"X-User-Id": uid}


def _events(http: httpx.Client, auth: dict, kinds: list[str]) -> list[dict]:
    resp = http.post(
        "/api/event-stream/query",
        headers=auth,
        json={"kinds": kinds, "limit": 100, "order": "asc"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# In-process state-machine tests — explicit `now=` for determinism.
# ---------------------------------------------------------------------------


def _run_helper(user_id: str, source: str, now: datetime) -> str:
    """Run the engagement helper from the test process. Returns the
    engagement_id (string)."""
    from persona.teacher.engagement import ensure_engagement_and_emit_turn
    from uuid import UUID

    async def _go():
        return await ensure_engagement_and_emit_turn(UUID(user_id), source=source, now=now)

    return str(asyncio.run(_go()))


def test_first_turn_emits_engagement_started_and_signal(http: httpx.Client, knowledge_url_env):
    uid, auth = _fresh_user(http)
    t0 = datetime.now(timezone.utc)
    eid = _run_helper(uid, "ask", t0)

    starts = _events(http, auth, ["user.engagement_started"])
    assert len(starts) == 1
    assert starts[0]["body"]["engagement_id"] == eid

    signals = _events(http, auth, ["signal.turn_arrived"])
    assert len(signals) == 1
    assert signals[0]["body"]["engagement_id"] == eid
    assert signals[0]["body"]["turn_source"] == "ask"

    # No premature end.
    ends = _events(http, auth, ["user.engagement_ended"])
    assert ends == []


def test_consecutive_turns_share_engagement(http: httpx.Client, knowledge_url_env):
    """Two turns within IDLE_THRESHOLD reuse the same engagement_id."""
    uid, auth = _fresh_user(http)
    t0 = datetime.now(timezone.utc)
    eid_1 = _run_helper(uid, "ask", t0)
    eid_2 = _run_helper(uid, "voice", t0 + timedelta(minutes=1))

    assert eid_1 == eid_2

    starts = _events(http, auth, ["user.engagement_started"])
    assert len(starts) == 1     # only the first turn emits started
    signals = _events(http, auth, ["signal.turn_arrived"])
    assert len(signals) == 2
    sources = [s["body"]["turn_source"] for s in signals]
    assert sources == ["ask", "voice"]


def test_idle_past_threshold_ends_and_starts_new(http: httpx.Client, knowledge_url_env):
    """A turn arriving > IDLE_THRESHOLD after the last activity but
    > RE_WINDOW after the implicit end starts a fresh engagement_id."""
    from persona.teacher.engagement import IDLE_THRESHOLD, RE_WINDOW

    uid, auth = _fresh_user(http)
    t0 = datetime.now(timezone.utc)
    eid_old = _run_helper(uid, "ask", t0)

    # Comfortably past both IDLE_THRESHOLD (ends the engagement) AND
    # RE_WINDOW (closes the re-engagement door).
    gap = IDLE_THRESHOLD + RE_WINDOW + timedelta(minutes=1)
    eid_new = _run_helper(uid, "ask", t0 + gap)

    assert eid_new != eid_old

    starts = _events(http, auth, ["user.engagement_started"])
    assert len(starts) == 2
    assert {s["body"]["engagement_id"] for s in starts} == {eid_old, eid_new}

    ends = _events(http, auth, ["user.engagement_ended"])
    assert len(ends) == 1
    assert ends[0]["body"]["engagement_id"] == eid_old


def test_re_engagement_within_window_reopens_same_id(http: httpx.Client, knowledge_url_env):
    """A turn arriving > IDLE_THRESHOLD but within RE_WINDOW of the
    implicit end reopens the SAME engagement_id."""
    from persona.teacher.engagement import IDLE_THRESHOLD, RE_WINDOW

    uid, auth = _fresh_user(http)
    t0 = datetime.now(timezone.utc)
    eid_old = _run_helper(uid, "ask", t0)

    # Trigger idle (gap > IDLE_THRESHOLD) but next turn arrives within
    # RE_WINDOW of the implicit end (gap - IDLE_THRESHOLD < RE_WINDOW).
    gap = IDLE_THRESHOLD + (RE_WINDOW / 2)   # implicit_end + half RE_WINDOW
    eid_continued = _run_helper(uid, "ask", t0 + gap)

    assert eid_continued == eid_old, "re-engagement window should reuse engagement_id"

    starts = _events(http, auth, ["user.engagement_started"])
    # First-time start + re-engagement start = 2, both with the same id.
    assert len(starts) == 2
    assert {s["body"]["engagement_id"] for s in starts} == {eid_old}

    ends = _events(http, auth, ["user.engagement_ended"])
    assert len(ends) == 1
    assert ends[0]["body"]["engagement_id"] == eid_old


# ---------------------------------------------------------------------------
# engagement_log view
# ---------------------------------------------------------------------------


def test_engagement_log_view_pairs_started_and_ended(http: httpx.Client, knowledge_url_env):
    """An ended engagement appears with both started_at + ended_at;
    a live engagement appears with ended_at = None."""
    from persona.teacher.engagement import IDLE_THRESHOLD, RE_WINDOW

    uid, auth = _fresh_user(http)
    t0 = datetime.now(timezone.utc)
    eid_old = _run_helper(uid, "ask", t0)
    gap = IDLE_THRESHOLD + RE_WINDOW + timedelta(minutes=1)
    eid_new = _run_helper(uid, "ask", t0 + gap)

    resp = http.get("/api/event-stream/views/engagement_log", headers=auth)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == 2

    by_id = {r["engagement_id"]: r for r in rows}
    # Closed engagement.
    assert by_id[eid_old]["started_at"] is not None
    assert by_id[eid_old]["ended_at"] is not None
    # Live engagement — no ended_at yet.
    assert by_id[eid_new]["started_at"] is not None
    assert by_id[eid_new]["ended_at"] is None


def test_engagement_log_view_404s_for_unknown(http: httpx.Client, auth: dict):
    resp = http.get("/api/event-stream/views/not_a_real_view", headers=auth)
    assert resp.status_code == 404
    assert "unknown view" in resp.json().get("detail", "")
    assert "engagement_log" in resp.json().get("detail", "")


def test_engagement_log_empty_for_fresh_user(http: httpx.Client):
    uid, auth = _fresh_user(http)
    resp = http.get("/api/event-stream/views/engagement_log", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Integration through /api/ask/stream — verifies the wire-up in ask.py
# ---------------------------------------------------------------------------


def test_ask_stream_fires_engagement_events(http: httpx.Client, knowledge_url_env):
    """A real ask turn (through the shell + persona sidecar) should
    produce engagement_started + signal.turn_arrived in the stream."""
    uid, auth = _fresh_user(http)
    body = {
        "question": "What's the role of ATP synthase?",
        "passage_text": "",
        "session_id": str(uuid.uuid4()),
    }
    with http.stream(
        "POST", "/api/ask/stream",
        headers={**auth, "Content-Type": "application/json"},
        json=body,
        timeout=60.0,
    ) as resp:
        assert resp.status_code == 200
        # Drain the SSE so the server-side write path completes.
        for _ in resp.iter_lines():
            pass

    starts = _events(http, auth, ["user.engagement_started"])
    assert len(starts) >= 1, "ask turn should have emitted engagement_started"

    signals = _events(http, auth, ["signal.turn_arrived"])
    assert len(signals) >= 1
    assert any(s["body"]["turn_source"] == "ask" for s in signals)


# ---------------------------------------------------------------------------
# Forbidden-metrics sweep — SPEC §17.5
# ---------------------------------------------------------------------------


def test_no_forbidden_metrics_in_any_emitted_event(http: httpx.Client, knowledge_url_env):
    """After driving a full PR-3 cycle (start → idle → reopen → new
    engagement), assert no event body carries any forbidden length/count
    metric. Catches accidental introduction of 'session length' style
    fields the Maestro could optimise toward."""
    from persona.teacher.engagement import IDLE_THRESHOLD, RE_WINDOW

    uid, auth = _fresh_user(http)
    t0 = datetime.now(timezone.utc)
    _run_helper(uid, "ask", t0)
    _run_helper(uid, "voice", t0 + timedelta(minutes=1))
    _run_helper(uid, "ask", t0 + IDLE_THRESHOLD + (RE_WINDOW / 2))   # re-engagement
    _run_helper(uid, "ask", t0 + IDLE_THRESHOLD + RE_WINDOW + timedelta(minutes=2))  # new

    # Fetch every event for this user — broad sweep across all kinds.
    resp = http.post(
        "/api/event-stream/query",
        headers=auth,
        json={"limit": 1000, "order": "asc"},
    )
    assert resp.status_code == 200, resp.text
    events = resp.json()
    assert len(events) > 0

    offenders: list[tuple[str, str]] = []
    for evt in events:
        body = evt.get("body") or {}
        for key in body.keys():
            if key in _FORBIDDEN_BODY_KEYS:
                offenders.append((evt["kind"], key))

    assert not offenders, (
        "forbidden length/count metrics found in event bodies:\n"
        + "\n".join(f"  - {kind}.body[{key!r}]" for kind, key in offenders)
        + "\nThese fields are exactly the dark-pattern optimisation "
        "targets SPEC §17 forbids the Maestro from rewarding."
    )
