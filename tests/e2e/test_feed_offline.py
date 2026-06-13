"""E2E for the offline-prepared landing feed.

Boots the worktree stack via the `services` fixture (LLM_PROVIDER=fake) and
exercises the structural changes:

  - the open path (`GET /api/feed`) is a pure read with the new `has_resumable`
    field, and never blocks on / triggers the LLM;
  - seeded cards come back ranked instantly;
  - the scheduler's `/api/feed-candidates/users` enumeration works;
  - manual refresh (`POST /api/feed/refresh`) returns immediately (the old code
    awaited the producer/LLM here);
  - the session-end webhook (`POST /api/maestro/event` on `engagement_ended`)
    still succeeds with the added feed-prep trigger.

The fake provider yields no real teacher cards (its `generate()` returns a fixed
non-JSON string → the recommender parses 0 items), so we seed cards directly to
exercise assemble/blend. The teacher-content path needs a real model.
"""
from __future__ import annotations

import time
import uuid

import httpx


def _client(url: str) -> httpx.Client:
    return httpx.Client(base_url=url, timeout=15.0, trust_env=False)


def _fresh_user(shell: httpx.Client) -> str:
    tag = int(time.time() * 1000)
    resp = shell.post(
        "/api/users", json={"username": f"e2e-feed-{tag}-{uuid.uuid4().hex[:6]}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_feed_offline_paths(services):
    base = services["base_port"]
    knowledge_url = f"http://127.0.0.1:{base + 2}"
    maestro_url = f"http://127.0.0.1:{base + 6}"

    with _client(services["shell_url"]) as shell, \
            _client(knowledge_url) as kn, \
            _client(maestro_url) as mo:
        uid = _fresh_user(shell)
        auth = {"X-User-Id": uid}

        # 1) Empty feed → pure read: new shape, stale, no cards, nothing resumable.
        body = mo.get("/api/feed", headers=auth)
        assert body.status_code == 200, body.text
        body = body.json()
        assert body["cards"] == []
        assert body["stale"] is True
        assert "has_resumable" in body  # proves the new assemble() is live
        assert body["has_resumable"] is False

        # 2) Seed one card directly, then the open path returns it ranked.
        card = {
            "source_persona": "teacher",
            "purpose": "teacher:e2e",
            "posture": "steady",
            "title": "Seeded e2e card",
            "opening": "A framing for the e2e test.",
            "intra_rank": 0.7,
            "category": "review",
        }
        r = kn.post("/api/feed-candidates", headers=auth, json=card)
        assert r.status_code == 200, r.text

        body = mo.get("/api/feed", headers=auth).json()
        assert len(body["cards"]) == 1
        assert body["cards"][0]["title"] == "Seeded e2e card"
        assert "blended_score" in body["cards"][0]
        assert body["stale"] is False  # fresh card → not stale

        # 3) Scheduler user-enumeration includes this user (my new endpoint).
        r = kn.get("/api/feed-candidates/users")
        assert r.status_code == 200, r.text
        assert uid in r.json()

        # 4) Manual refresh returns immediately — non-blocking. The old code
        #    awaited the producer (and thus the LLM) here.
        t0 = time.monotonic()
        r = mo.post("/api/feed/refresh", headers=auth)
        elapsed = time.monotonic() - t0
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True
        assert elapsed < 5.0

        # 5) Session-end webhook drives the long instance AND fires the feed-prep
        #    trigger; must succeed and still return the kickoff decision.
        ended = {
            "event": {
                "event_id": str(uuid.uuid4()),
                "user_id": uid,
                "ts": "2026-06-13T12:00:00+00:00",
                "valid_at": None,
                "source": "user",
                "kind": "user.engagement_ended",
                "body": {"engagement_id": str(uuid.uuid4())},
                "refs": {},
                "schema_version": 1,
            }
        }
        r = mo.post("/api/maestro/event", json=ended)
        assert r.status_code == 200, r.text
        assert "decision" in r.json()
