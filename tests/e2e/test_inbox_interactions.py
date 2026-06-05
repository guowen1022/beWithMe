"""E2E for PR-7 inbox interaction events + stock cap + TTL + view.

Three surfaces:
  1. Every status transition (tap, dismiss, consume) emits the
     corresponding user.proposal_* event.
  2. POST /api/inbox over STOCK_CAP auto-expires the oldest with a
     system.proposal_expired event (reason='stock_cap').
  3. TTL sweep on GET /api/inbox lazily expires anything older than
     TTL_HOURS with reason='ttl'.
  4. inbox_interaction_log view chronologically lists every transition.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest


def _fresh_user(http: httpx.Client) -> tuple[str, dict]:
    tag = int(time.time() * 1000)
    resp = http.post("/api/users", json={"username": f"e2e-iact-{tag}-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"], {"X-User-Id": resp.json()["id"]}


def _new_proposal(http: httpx.Client, auth: dict, **kw) -> dict:
    payload = {
        "kickoff_event_id": str(uuid.uuid4()),
        "title": "t", "persona_purpose": "teacher:p",
        "posture": "steady", "opening": "o",
    }
    payload.update(kw)
    resp = http.post("/api/inbox", headers=auth, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(http: httpx.Client, auth: dict, kinds: list[str]) -> list[dict]:
    resp = http.post(
        "/api/event-stream/query",
        headers=auth, json={"kinds": kinds, "limit": 100, "order": "asc"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Interaction events ----------------------------------------------------


def test_tap_emits_proposal_tapped_event(http: httpx.Client):
    uid, auth = _fresh_user(http)
    p = _new_proposal(http, auth, title="tap me")
    http.post(f"/api/inbox/{p['id']}/tap", headers=auth).raise_for_status()
    rows = _events(http, auth, ["user.proposal_tapped"])
    assert len(rows) == 1
    body = rows[0]["body"]
    assert body["proposal_id"] == p["id"]
    assert body["kickoff_event_id"] == p["kickoff_event_id"]


def test_dismiss_emits_proposal_dismissed_event(http: httpx.Client):
    uid, auth = _fresh_user(http)
    p = _new_proposal(http, auth, title="dismiss me")
    http.post(f"/api/inbox/{p['id']}/dismiss", headers=auth).raise_for_status()
    rows = _events(http, auth, ["user.proposal_dismissed"])
    assert len(rows) == 1
    assert rows[0]["body"]["proposal_id"] == p["id"]


def test_consume_emits_proposal_consumed_event(http: httpx.Client):
    uid, auth = _fresh_user(http)
    p = _new_proposal(http, auth, title="consume me")
    http.post(f"/api/inbox/{p['id']}/tap", headers=auth).raise_for_status()
    http.post(f"/api/inbox/{p['id']}/consume", headers=auth).raise_for_status()
    rows = _events(http, auth, ["user.proposal_consumed"])
    assert len(rows) == 1


def test_idempotent_tap_does_not_emit_duplicate_events(http: httpx.Client):
    uid, auth = _fresh_user(http)
    p = _new_proposal(http, auth, title="solo")
    for _ in range(3):
        http.post(f"/api/inbox/{p['id']}/tap", headers=auth).raise_for_status()
    rows = _events(http, auth, ["user.proposal_tapped"])
    assert len(rows) == 1


# --- Stock cap -------------------------------------------------------------


def test_stock_cap_expires_oldest_when_over_M(http: httpx.Client):
    from services.knowledge.routers.inbox import STOCK_CAP

    uid, auth = _fresh_user(http)
    # Create STOCK_CAP+2 pending proposals; the two oldest should auto-expire.
    created: list[dict] = []
    for i in range(STOCK_CAP + 2):
        p = _new_proposal(http, auth, title=f"P{i}")
        created.append(p)

    # Listing pending should cap at STOCK_CAP.
    pending = http.get("/api/inbox?status=pending", headers=auth).json()
    assert len(pending) == STOCK_CAP

    # The two earliest proposals are expired (with stock_cap reason).
    expired_rows = _events(http, auth, ["system.proposal_expired"])
    assert len(expired_rows) == 2
    reasons = {r["body"]["reason"] for r in expired_rows}
    assert reasons == {"stock_cap"}
    expired_ids = {r["body"]["proposal_id"] for r in expired_rows}
    assert expired_ids == {created[0]["id"], created[1]["id"]}


# --- TTL sweep -------------------------------------------------------------


def test_ttl_sweep_expires_old_pending_on_list(http: httpx.Client):
    """Force an old-pending row via direct DB writeback, then GET /api/inbox
    triggers the lazy sweep."""
    import asyncio
    import os
    import asyncpg
    from urllib.parse import urlparse
    import infra.config  # populates os.environ

    uid, auth = _fresh_user(http)
    p = _new_proposal(http, auth, title="old one")

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("DATABASE_URL not set")
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(sync_url)

    async def _age_row():
        conn = await asyncpg.connect(
            host=parsed.hostname, port=parsed.port or 5432,
            user=parsed.username, password=parsed.password,
            database=parsed.path.lstrip("/"),
        )
        try:
            old_ts = datetime.now(timezone.utc) - timedelta(hours=25)
            await conn.execute(
                "UPDATE inbox_proposals SET created_at=$1 WHERE id=$2",
                old_ts, uuid.UUID(p["id"]),
            )
        finally:
            await conn.close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_age_row())
    finally:
        loop.close()

    # Trigger the lazy sweep.
    listing = http.get("/api/inbox", headers=auth).json()
    target = next(r for r in listing if r["id"] == p["id"])
    assert target["status"] == "expired"

    rows = _events(http, auth, ["system.proposal_expired"])
    ttl_rows = [r for r in rows if r["body"].get("reason") == "ttl"]
    assert any(r["body"]["proposal_id"] == p["id"] for r in ttl_rows)


# --- View ------------------------------------------------------------------


def test_inbox_interaction_log_lists_all_kinds(http: httpx.Client):
    uid, auth = _fresh_user(http)
    # Cover: tap, consume, dismiss.
    p1 = _new_proposal(http, auth, title="taps")
    p2 = _new_proposal(http, auth, title="dismisses")
    http.post(f"/api/inbox/{p1['id']}/tap", headers=auth).raise_for_status()
    http.post(f"/api/inbox/{p1['id']}/consume", headers=auth).raise_for_status()
    http.post(f"/api/inbox/{p2['id']}/dismiss", headers=auth).raise_for_status()

    resp = http.get("/api/event-stream/views/inbox_interaction_log", headers=auth)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    kinds = [r["kind"] for r in rows]
    assert kinds == [
        "user.proposal_tapped",
        "user.proposal_consumed",
        "user.proposal_dismissed",
    ]
    # Each row carries the proposal/kickoff linkage.
    assert all(r.get("kickoff_event_id") for r in rows)
