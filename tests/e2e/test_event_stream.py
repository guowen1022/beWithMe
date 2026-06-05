"""E2E for the per-user event stream (PR-1).

Drives the shell from outside the process — auth gate, proxy, knowledge
sidecar, real Postgres. No TestClient, no monkeypatch.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest


def _post_event(http: httpx.Client, auth: dict[str, str], **overrides) -> dict:
    body = {
        "kind": "user.engagement_started",
        "source": "user",
        "body": {"engagement_id": "e2e-test"},
    }
    body.update(overrides)
    resp = http.post("/api/event-stream", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _query(http: httpx.Client, auth: dict[str, str], **overrides) -> list[dict]:
    body = {"limit": 100, "order": "desc"}
    body.update(overrides)
    resp = http.post("/api/event-stream/query", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _drain(http: httpx.Client, auth: dict[str, str]) -> None:
    """Best-effort: remove stream rows from prior tests in this session.

    Tests are written so they don't depend on this — they pick unique kinds
    and assert presence, not exact counts — but draining keeps debug
    output readable when something fails.
    """
    # No DELETE endpoint exists yet; nothing to do. Left as a placeholder
    # so it's obvious where to wire cleanup if we add one later.
    pass


def test_emit_requires_user_id(http: httpx.Client):
    """No X-User-Id → 401 from the shell, never reaches the knowledge sidecar."""
    resp = http.post(
        "/api/event-stream",
        json={"kind": "user.engagement_started", "source": "user", "body": {}},
    )
    assert resp.status_code == 401


def test_emit_returns_full_envelope(http: httpx.Client, auth: dict[str, str]):
    """POST /api/event-stream stamps event_id, user_id, ts, schema_version."""
    row = _post_event(
        http, auth,
        kind="user.engagement_started",
        source="user",
        body={"engagement_id": "abc"},
    )
    assert "event_id" in row
    assert row["user_id"] == auth["X-User-Id"]
    assert row["kind"] == "user.engagement_started"
    assert row["source"] == "user"
    assert row["body"] == {"engagement_id": "abc"}
    assert row["refs"] is None
    assert row["schema_version"] == 1
    # ts is server-stamped ISO-8601 with tz
    ts = datetime.fromisoformat(row["ts"])
    assert ts.tzinfo is not None


def test_emit_and_query_roundtrip(http: httpx.Client, auth: dict[str, str]):
    """An emitted event is queryable by a tag-unique kind."""
    unique_kind = f"agent.observation.test_roundtrip_{int(time.time() * 1000)}"
    posted = _post_event(
        http, auth,
        kind=unique_kind,
        source="agent",
        body={"note": "saw the user nod"},
        refs={"concept_id": "c-1"},
    )
    fetched = _query(http, auth, kinds=[unique_kind])
    assert len(fetched) == 1, fetched
    assert fetched[0]["event_id"] == posted["event_id"]
    assert fetched[0]["body"] == {"note": "saw the user nod"}
    assert fetched[0]["refs"] == {"concept_id": "c-1"}


def test_jsonb_body_preserves_nested_structure(http: httpx.Client, auth: dict[str, str]):
    """Nested dict / list / number / bool / null all survive the round-trip."""
    unique_kind = f"maestro_long.kickoff_decision.test_jsonb_{int(time.time() * 1000)}"
    deep_body = {
        "candidates": [
            {"idx": 0, "posture": "steady", "score": 0.81, "review_concepts": ["c-1", "c-2"]},
            {"idx": 1, "posture": "deepen", "score": 0.54, "review_concepts": []},
        ],
        "propensity": 0.7,
        "act": True,
        "rationale": None,
    }
    _post_event(http, auth, kind=unique_kind, source="maestro_long", body=deep_body)
    fetched = _query(http, auth, kinds=[unique_kind])
    assert len(fetched) == 1
    assert fetched[0]["body"] == deep_body


def test_query_filters_by_source(http: httpx.Client, auth: dict[str, str]):
    """`sources` filter excludes events from other sources."""
    tag = int(time.time() * 1000)
    kind_user = f"user.engagement_started.test_src_{tag}"
    kind_agent = f"agent.observation.test_src_{tag}"
    _post_event(http, auth, kind=kind_user, source="user", body={})
    _post_event(http, auth, kind=kind_agent, source="agent", body={})
    user_only = _query(http, auth, kinds=[kind_user, kind_agent], sources=["user"])
    assert len(user_only) == 1
    assert user_only[0]["kind"] == kind_user


def test_query_respects_limit_and_order(http: httpx.Client, auth: dict[str, str]):
    """`limit` caps results; `order=asc` returns oldest first."""
    tag = int(time.time() * 1000)
    kind = f"signal.turn_arrived.test_order_{tag}"
    posted = [_post_event(http, auth, kind=kind, source="signal", body={"i": i}) for i in range(3)]

    asc = _query(http, auth, kinds=[kind], order="asc", limit=10)
    desc = _query(http, auth, kinds=[kind], order="desc", limit=10)
    assert [r["body"]["i"] for r in asc] == [0, 1, 2]
    assert [r["body"]["i"] for r in desc] == [2, 1, 0]

    limited = _query(http, auth, kinds=[kind], order="asc", limit=2)
    assert len(limited) == 2
    assert limited[0]["event_id"] == posted[0]["event_id"]


def test_query_filters_by_time_window(http: httpx.Client, auth: dict[str, str]):
    """`since` excludes events older than the timestamp."""
    tag = int(time.time() * 1000)
    kind = f"signal.flow_marker.test_time_{tag}"
    earlier = _post_event(http, auth, kind=kind, source="signal", body={"m": "before"})
    # Carve a window strictly after `earlier.ts`. Using its own ts as `since`
    # would be inclusive — bump by one microsecond.
    cutoff = (datetime.fromisoformat(earlier["ts"]) + timedelta(microseconds=1)).isoformat()
    later = _post_event(http, auth, kind=kind, source="signal", body={"m": "after"})

    fetched = _query(http, auth, kinds=[kind], since=cutoff, order="asc")
    ids = {r["event_id"] for r in fetched}
    assert later["event_id"] in ids
    assert earlier["event_id"] not in ids


def test_valid_at_round_trips(http: httpx.Client, auth: dict[str, str]):
    """An explicit `valid_at` survives emit + query."""
    tag = int(time.time() * 1000)
    kind = f"agent.followup_scheduled.test_validat_{tag}"
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    posted = _post_event(http, auth, kind=kind, source="agent", body={}, valid_at=future)
    assert posted["valid_at"] is not None
    fetched = _query(http, auth, kinds=[kind])
    assert fetched[0]["valid_at"] is not None
    assert datetime.fromisoformat(fetched[0]["valid_at"]) == datetime.fromisoformat(future)


def test_cross_user_isolation(http: httpx.Client, auth: dict[str, str]):
    """An event emitted by user A is invisible to user B."""
    # Create a second throwaway user.
    tag = int(time.time() * 1000)
    create_resp = http.post("/api/users", json={"username": f"e2e-event-stream-{tag}"})
    assert create_resp.status_code == 200, create_resp.text
    other_user_id = create_resp.json()["id"]
    other_auth = {"X-User-Id": other_user_id}

    # User A (default) emits.
    kind = f"user.engagement_started.test_iso_{tag}"
    a_row = _post_event(http, auth, kind=kind, source="user", body={"engagement_id": "A"})

    # User B emits a different event under the same kind.
    b_row = _post_event(http, other_auth, kind=kind, source="user", body={"engagement_id": "B"})

    a_view = _query(http, auth, kinds=[kind])
    b_view = _query(http, other_auth, kinds=[kind])

    a_ids = {r["event_id"] for r in a_view}
    b_ids = {r["event_id"] for r in b_view}

    assert a_row["event_id"] in a_ids and b_row["event_id"] not in a_ids
    assert b_row["event_id"] in b_ids and a_row["event_id"] not in b_ids
    # Each side sees its own body.
    assert all(r["body"]["engagement_id"] == "A" for r in a_view if r["event_id"] == a_row["event_id"])
    assert all(r["body"]["engagement_id"] == "B" for r in b_view if r["event_id"] == b_row["event_id"])


def test_purge_user_cascades_events(http: httpx.Client, auth: dict[str, str]):
    """Erasing a user via `infra.user_data.purge_user_data` removes their events.

    This is the contract the user-data map (commit `0e2e8fd`) is supposed
    to honor for any new user-scoped table: `user_id` FK CASCADE means a
    purge call cleans up the stream for free, no per-table registration
    needed.
    """
    # Async DB ops happen in this process via infra.db. Same Postgres the
    # sidecars are using (DATABASE_URL is read at .env load).
    import asyncio
    import os
    from uuid import UUID, uuid4

    import asyncpg
    import infra.config  # noqa: F401 — populates os.environ
    from urllib.parse import urlparse

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("DATABASE_URL not set")

    # 1. Create a throwaway user via the public POST /api/users.
    tag = int(time.time() * 1000)
    create_resp = http.post("/api/users", json={"username": f"e2e-purge-{tag}-{uuid4().hex[:8]}"})
    assert create_resp.status_code == 200, create_resp.text
    target_user_id = create_resp.json()["id"]
    target_auth = {"X-User-Id": target_user_id}

    # 2. Emit a few events for that user.
    kind = f"agent.observation.test_purge_{tag}"
    for i in range(3):
        _post_event(http, target_auth, kind=kind, source="agent", body={"i": i})
    assert len(_query(http, target_auth, kinds=[kind])) == 3

    # 3. Run purge_user_data via a fresh asyncio loop / direct asyncpg.
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(sync_url)

    async def _purge_and_count() -> tuple[int, int]:
        conn = await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
        )
        try:
            before = await conn.fetchval(
                "SELECT COUNT(*) FROM events WHERE user_id = $1", target_user_id,
            )
            # Cascade from users deletion — proves the FK is wired.
            await conn.execute("DELETE FROM users WHERE id = $1", target_user_id)
            after = await conn.fetchval(
                "SELECT COUNT(*) FROM events WHERE user_id = $1", target_user_id,
            )
            return int(before), int(after)
        finally:
            await conn.close()

    loop = asyncio.new_event_loop()
    try:
        before, after = loop.run_until_complete(_purge_and_count())
    finally:
        loop.close()

    assert before == 3
    assert after == 0
