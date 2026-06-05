"""E2E for the Maestro sidecar (PR-4).

Three surfaces:
  1. Sidecar boots — /api/maestro/health returns 200.
  2. Webhook → decision → stream — POST /api/maestro/event with a synthetic
     event produces a `maestro_long.kickoff_decision` row.
  3. View — GET /api/event-stream/views/kickoff_log lists the decisions.

The fake LLM provider (`LLM_PROVIDER=fake`, set by conftest) returns
`{"fake": true}` for `generate_json`, which the candidates parser
treats as an empty list. ACT paths therefore degrade to a logged
SILENCE-with-explanation (per long.py). That's the right Phase-0
behavior to assert on: the gate fires, the substrate retrieval runs,
and the decision is recorded — without depending on a real LLM during
tests.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest


def _fresh_user(http: httpx.Client) -> tuple[str, dict]:
    tag = int(time.time() * 1000)
    resp = http.post("/api/users", json={"username": f"e2e-maestro-{tag}-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    uid = resp.json()["id"]
    return uid, {"X-User-Id": uid}


def _post_event(http: httpx.Client, auth: dict, **kw) -> dict:
    body = {
        "kind": "agent.observation",
        "source": "agent",
        "body": {},
    }
    body.update(kw)
    resp = http.post("/api/event-stream", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _query(http: httpx.Client, auth: dict, kinds: list[str]) -> list[dict]:
    resp = http.post(
        "/api/event-stream/query",
        headers=auth,
        json={"kinds": kinds, "limit": 100, "order": "asc"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_maestro_health(services):
    """Sidecar booted and /api/maestro/health responds."""
    with httpx.Client(base_url=services["shell_url"], timeout=10.0, trust_env=False) as h:
        resp = h.get("/api/maestro/health", headers={"X-User-Id": "00000000-0000-0000-0000-000000000000"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["service"] == "maestro"
        assert body["ok"] is True
        assert "cache_size" in body


def test_webhook_for_unknown_kind_emits_silence(http: httpx.Client, services):
    """Default rule path: an unknown event kind → SILENCE decision."""
    uid, auth = _fresh_user(http)

    # Seed: emit a vanilla event that the Maestro can echo back.
    seed = _post_event(http, auth, kind="agent.observation", body={"topic": "test"})

    # Maestro webhook is reachable through the shell at /api/maestro/event.
    resp = http.post(
        "/api/maestro/event",
        headers=auth,
        json={"event": seed},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["decision"] == "SILENCE"
    assert payload["k"] == 0

    decisions = _query(http, auth, ["maestro_long.kickoff_decision"])
    assert len(decisions) == 1
    body = decisions[0]["body"]
    assert body["decision"] == "SILENCE"
    assert body["triggering_event_id"] == seed["event_id"]
    assert body["k"] == 0
    assert body["candidates"] == []
    # Propensity recorded for PR-8's off-policy training.
    assert 0.0 < body["propensity"] <= 1.0


def test_webhook_for_capture_attempts_act_then_falls_back_silently(http: httpx.Client):
    """A capture.* kind triggers ACT; with the fake LLM returning no
    candidates, the long instance gracefully records SILENCE-with-
    rationale rather than emitting empty ACT."""
    uid, auth = _fresh_user(http)

    seed = _post_event(http, auth, kind="capture.created", source="capture", body={"capture_id": "c-1"})

    resp = http.post(
        "/api/maestro/event",
        headers=auth,
        json={"event": seed},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # The fake LLM's generate_json returns {"fake": true} which the
    # candidate parser handles as "no candidates" → SILENCE.
    assert payload["decision"] == "SILENCE"

    decisions = _query(http, auth, ["maestro_long.kickoff_decision"])
    assert len(decisions) == 1
    body = decisions[0]["body"]
    # The fallback rationale carries the original ACT reason + the
    # "candidate generation returned 0" suffix.
    assert "capture event" in body["rationale"]
    assert "candidate generation returned 0" in body["rationale"]


def test_engagement_end_via_helper_fires_maestro_webhook(http: httpx.Client, services):
    """When the engagement helper writes an engagement_ended event, it
    also POSTs the maestro webhook. End-to-end: t0 → idle gap → next
    turn → engagement_ended emit + maestro decision emit."""
    from persona.teacher.engagement import (
        IDLE_THRESHOLD, RE_WINDOW, ensure_engagement_and_emit_turn,
    )
    import asyncio

    uid, auth = _fresh_user(http)

    async def _drive() -> None:
        t0 = datetime.now(timezone.utc)
        await ensure_engagement_and_emit_turn(uuid.UUID(uid), "ask", now=t0)
        # Past IDLE_THRESHOLD AND past RE_WINDOW → start fresh engagement,
        # which means the prior one is implicitly ended.
        await ensure_engagement_and_emit_turn(
            uuid.UUID(uid), "ask",
            now=t0 + IDLE_THRESHOLD + RE_WINDOW + timedelta(minutes=2),
        )

    # Point SiliconBrainClient + maestro upstream at the test sidecars.
    prior_knowledge = os.environ.get("KNOWLEDGE_SERVICE_URL")
    prior_maestro = os.environ.get("MAESTRO_SERVICE_URL")
    os.environ["KNOWLEDGE_SERVICE_URL"] = services["knowledge_url"]
    os.environ["MAESTRO_SERVICE_URL"] = (
        f"http://127.0.0.1:{services['base_port'] + 6}"
    )
    try:
        asyncio.run(_drive())
    finally:
        if prior_knowledge is None:
            os.environ.pop("KNOWLEDGE_SERVICE_URL", None)
        else:
            os.environ["KNOWLEDGE_SERVICE_URL"] = prior_knowledge
        if prior_maestro is None:
            os.environ.pop("MAESTRO_SERVICE_URL", None)
        else:
            os.environ["MAESTRO_SERVICE_URL"] = prior_maestro

    # engagement_ended landed
    ends = _query(http, auth, ["user.engagement_ended"])
    assert len(ends) == 1

    # ... and the Maestro logged a decision triggered by it.
    decisions = _query(http, auth, ["maestro_long.kickoff_decision"])
    assert len(decisions) >= 1
    triggered = [
        d for d in decisions
        if d["body"].get("triggering_event_id") == ends[0]["event_id"]
    ]
    assert triggered, (
        "Maestro should have produced a kickoff_decision triggered by the "
        "engagement_ended event"
    )


def test_kickoff_log_view_lists_decisions(http: httpx.Client):
    uid, auth = _fresh_user(http)
    seed = _post_event(http, auth, kind="agent.observation", body={"x": 1})
    http.post(
        "/api/maestro/event", headers=auth, json={"event": seed},
    ).raise_for_status()

    resp = http.get("/api/event-stream/views/kickoff_log", headers=auth)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "SILENCE"
    assert row["triggering_event_id"] == seed["event_id"]
    assert row["k"] == 0
    assert row["candidates"] == []
