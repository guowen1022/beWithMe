"""E2E for the PR-5 inbox + cache wire.

Three surfaces:
  1. Inbox lifecycle — create, list, tap, dismiss, consume.
  2. Maestro cache HTTP — set / get / 404 when empty.
  3. The full kickoff → realize → tap → cache-seed → engagement-prompt
     loop, with the maestro's ACT-with-zero-candidates fallback handled
     by manually injecting a kickoff_decision event so we can test the
     realization path without a real LLM.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest


def _fresh_user(http: httpx.Client) -> tuple[str, dict]:
    tag = int(time.time() * 1000)
    resp = http.post("/api/users", json={"username": f"e2e-inbox-{tag}-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    uid = resp.json()["id"]
    return uid, {"X-User-Id": uid}


# ---------------------------------------------------------------------------
# Inbox lifecycle
# ---------------------------------------------------------------------------


def test_inbox_create_list_tap_consume(http: httpx.Client):
    uid, auth = _fresh_user(http)
    payload = {
        "kickoff_event_id": str(uuid.uuid4()),
        "candidate_idx": 0,
        "title": "Short reinforcement on attention",
        "persona_purpose": "teacher:long-horizon-propose",
        "posture": "steady",
        "opening": "Attention is the next reachable concept; ~5 min.",
        "body": {"concept": "self-attention"},
    }
    created = http.post("/api/inbox", headers=auth, json=payload)
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["status"] == "pending"
    assert row["posture"] == "steady"

    listing = http.get("/api/inbox", headers=auth).json()
    assert any(p["id"] == row["id"] for p in listing)

    pid = row["id"]
    tap = http.post(f"/api/inbox/{pid}/tap", headers=auth)
    assert tap.status_code == 200
    assert tap.json()["status"] == "tapped"
    assert tap.json()["tapped_at"] is not None

    # Idempotent re-tap.
    assert http.post(f"/api/inbox/{pid}/tap", headers=auth).json()["status"] == "tapped"

    cons = http.post(f"/api/inbox/{pid}/consume", headers=auth)
    assert cons.status_code == 200
    assert cons.json()["status"] == "consumed"
    assert cons.json()["consumed_at"] is not None

    # Re-consume idempotent.
    assert http.post(f"/api/inbox/{pid}/consume", headers=auth).json()["status"] == "consumed"


def test_inbox_dismiss_blocks_consume(http: httpx.Client):
    uid, auth = _fresh_user(http)
    created = http.post("/api/inbox", headers=auth, json={
        "kickoff_event_id": str(uuid.uuid4()),
        "title": "x", "persona_purpose": "teacher:x",
        "posture": "steady", "opening": "y",
    }).json()
    pid = created["id"]
    dismiss = http.post(f"/api/inbox/{pid}/dismiss", headers=auth)
    assert dismiss.status_code == 200
    assert dismiss.json()["status"] == "dismissed"

    # consume from dismissed is 409.
    bad = http.post(f"/api/inbox/{pid}/consume", headers=auth)
    assert bad.status_code == 409


def test_inbox_isolation(http: httpx.Client):
    uid_a, auth_a = _fresh_user(http)
    uid_b, auth_b = _fresh_user(http)
    a_row = http.post("/api/inbox", headers=auth_a, json={
        "kickoff_event_id": str(uuid.uuid4()),
        "title": "A's only", "persona_purpose": "teacher:x",
        "posture": "steady", "opening": "for A",
    }).json()
    # B can't see A's row.
    b_list = http.get("/api/inbox", headers=auth_b).json()
    assert all(p["id"] != a_row["id"] for p in b_list)
    # B can't tap A's row.
    bad = http.post(f"/api/inbox/{a_row['id']}/tap", headers=auth_b)
    assert bad.status_code == 404


# ---------------------------------------------------------------------------
# Maestro cache HTTP
# ---------------------------------------------------------------------------


def test_maestro_cache_set_then_get(http: httpx.Client, services):
    uid, auth = _fresh_user(http)
    purpose = "teacher:long-horizon-propose"
    set_resp = http.post(
        "/api/maestro/cache", headers=auth,
        json={
            "persona_purpose": purpose,
            "paragraph": "Short reinforcement on attention.",
            "posture": "deepen",
            "candidate_idx": 0,
        },
    )
    assert set_resp.status_code == 200, set_resp.text
    body = set_resp.json()
    assert body["posture"] == "deepen"
    assert body["candidate_idx"] == 0

    get_resp = http.get(
        "/api/maestro/cache",
        headers=auth, params={"persona_purpose": purpose},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["paragraph"].startswith("Short")
    assert get_resp.json()["posture"] == "deepen"


def test_maestro_cache_get_404_for_empty(http: httpx.Client):
    uid, auth = _fresh_user(http)
    resp = http.get(
        "/api/maestro/cache",
        headers=auth, params={"persona_purpose": "teacher:nothing-here"},
    )
    assert resp.status_code == 404


def test_maestro_cache_rejects_unknown_posture(http: httpx.Client):
    uid, auth = _fresh_user(http)
    resp = http.post(
        "/api/maestro/cache", headers=auth,
        json={"persona_purpose": "x", "paragraph": "y", "posture": "made_up"},
    )
    assert resp.status_code == 400


def test_maestro_cache_isolation(http: httpx.Client):
    uid_a, auth_a = _fresh_user(http)
    uid_b, auth_b = _fresh_user(http)
    purpose = "teacher:long-horizon-propose"
    http.post("/api/maestro/cache", headers=auth_a, json={
        "persona_purpose": purpose, "paragraph": "for A", "posture": "steady",
    }).raise_for_status()
    # B can't see A's entry.
    b = http.get("/api/maestro/cache", headers=auth_b, params={"persona_purpose": purpose})
    assert b.status_code == 404


# ---------------------------------------------------------------------------
# Full kickoff → realize → tap → engagement seeding
# ---------------------------------------------------------------------------


def test_realize_kickoff_writes_K_proposals(http: httpx.Client, services):
    """POST /api/agent/kickoff produces K inbox rows clustered by
    kickoff_event_id. Idempotent on re-fire."""
    uid, auth = _fresh_user(http)
    kickoff_event_id = str(uuid.uuid4())
    payload = {
        "kickoff_event_id": kickoff_event_id,
        "user_id": uid,
        "candidates": [
            {
                "title": "Review attention", "posture": "steady",
                "persona_purpose": "teacher:long-horizon-propose",
                "opening": "Light recall on attention; ~5 min.",
            },
            {
                "title": "Multi-head extension", "posture": "deepen",
                "persona_purpose": "teacher:long-horizon-propose",
                "opening": "Extend attention_starter.py with multi-head.",
            },
        ],
    }
    resp = http.post("/api/agent/kickoff", headers=auth, json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["written"] == 2

    # Both rows visible and clustered.
    rows = http.get("/api/inbox", headers=auth).json()
    cluster = [r for r in rows if r["kickoff_event_id"] == kickoff_event_id]
    assert len(cluster) == 2
    by_idx = sorted(cluster, key=lambda r: r["candidate_idx"])
    assert [r["posture"] for r in by_idx] == ["steady", "deepen"]

    # Re-fire is idempotent.
    resp2 = http.post("/api/agent/kickoff", headers=auth, json=payload)
    assert resp2.json()["skipped"] == 2
    assert len(http.get("/api/inbox", headers=auth).json()) == 2


def test_engagement_seeds_cache_from_tapped_proposal(http: httpx.Client, services):
    """End-to-end: write a proposal → user taps it → engagement helper
    runs and the maestro cache holds the candidate's posture + opening."""
    from persona.teacher.engagement import ensure_engagement_and_emit_turn

    uid, auth = _fresh_user(http)

    # Seed: write one proposal and tap it.
    payload = {
        "kickoff_event_id": str(uuid.uuid4()),
        "title": "Deep attention dive", "persona_purpose": "teacher:long-horizon-propose",
        "posture": "deepen", "opening": "Deepen on attention now.",
    }
    proposal = http.post("/api/inbox", headers=auth, json=payload).json()
    http.post(f"/api/inbox/{proposal['id']}/tap", headers=auth).raise_for_status()
    # Sanity: status is tapped.
    assert http.get("/api/inbox", headers=auth).json()[0]["status"] == "tapped"

    # Run the engagement helper — point client+maestro at the test sidecars.
    prior_k = os.environ.get("KNOWLEDGE_SERVICE_URL")
    prior_m = os.environ.get("MAESTRO_SERVICE_URL")
    os.environ["KNOWLEDGE_SERVICE_URL"] = services["knowledge_url"]
    os.environ["MAESTRO_SERVICE_URL"] = f"http://127.0.0.1:{services['base_port'] + 6}"
    try:
        asyncio.run(ensure_engagement_and_emit_turn(uuid.UUID(uid), "ask"))
    finally:
        if prior_k is None:
            os.environ.pop("KNOWLEDGE_SERVICE_URL", None)
        else:
            os.environ["KNOWLEDGE_SERVICE_URL"] = prior_k
        if prior_m is None:
            os.environ.pop("MAESTRO_SERVICE_URL", None)
        else:
            os.environ["MAESTRO_SERVICE_URL"] = prior_m

    # Proposal now marked consumed.
    after = http.get("/api/inbox", headers=auth).json()
    target = next(p for p in after if p["id"] == proposal["id"])
    assert target["status"] == "consumed"

    # Maestro cache holds the candidate's frame.
    cache = http.get(
        "/api/maestro/cache",
        headers=auth, params={"persona_purpose": "teacher:long-horizon-propose"},
    )
    assert cache.status_code == 200, cache.text
    body = cache.json()
    assert body["posture"] == "deepen"
    assert body["paragraph"] == "Deepen on attention now."
