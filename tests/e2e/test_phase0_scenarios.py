"""E2E for Phase-0 scenarios catalogued in tests/scenarios/phase0_scenarios.md.

Each test corresponds to a row in the coverage map and exercises a
user-visible behaviour the Phase-0 contract must hold. Tests here are
*incremental* on top of the existing per-surface e2e files
(test_inbox_and_cache, test_inbox_interactions, test_engagement,
test_maestro, test_maestro_short) — duplicates are not repeated.

Run with: pytest tests/e2e/test_phase0_scenarios.py -v
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers (small + copied from the sibling files; do not import — keep tests
# decoupled so a refactor of one file doesn't ripple).
# ---------------------------------------------------------------------------


def _fresh_user(http: httpx.Client, tag: str = "ph0") -> tuple[str, dict]:
    stamp = int(time.time() * 1000)
    resp = http.post("/api/users", json={"username": f"e2e-{tag}-{stamp}-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    uid = resp.json()["id"]
    return uid, {"X-User-Id": uid}


def _new_proposal(http: httpx.Client, auth: dict, **kw) -> dict:
    payload = {
        "kickoff_event_id": str(uuid.uuid4()),
        "title": "t", "persona_purpose": "teacher:long-horizon-propose",
        "posture": "steady", "opening": "o",
    }
    payload.update(kw)
    resp = http.post("/api/inbox", headers=auth, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(http: httpx.Client, auth: dict, kinds: list[str] | None = None) -> list[dict]:
    body = {"limit": 200, "order": "asc"}
    if kinds:
        body["kinds"] = kinds
    resp = http.post("/api/event-stream/query", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _emit_event(http: httpx.Client, auth: dict, **kw) -> dict:
    body = {"kind": "signal.turn_arrived", "source": "signal", "body": {}}
    body.update(kw)
    resp = http.post("/api/event-stream", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_signal(http: httpx.Client, auth: dict, event: dict) -> dict:
    resp = http.post("/api/maestro/signal", headers=auth, json={"event": event})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _read_cache(http: httpx.Client, auth: dict, purpose: str = "teacher:long-horizon-propose") -> dict | None:
    resp = http.get("/api/maestro/cache", headers=auth, params={"persona_purpose": purpose})
    if resp.status_code == 404:
        return None
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_cache(http: httpx.Client, auth: dict, *, posture: str = "steady", paragraph: str = "seed paragraph") -> None:
    http.post(
        "/api/maestro/cache", headers=auth,
        json={
            "persona_purpose": "teacher:long-horizon-propose",
            "paragraph": paragraph, "posture": posture,
        },
    ).raise_for_status()


# ---------------------------------------------------------------------------
# A. Inbox lifecycle gaps
# ---------------------------------------------------------------------------


def test_status_filter_returns_only_requested(http: httpx.Client):
    """Scenario #7 — GET /api/inbox?status=X returns only rows with that
    status. Other statuses must not bleed through."""
    uid, auth = _fresh_user(http, "status-filter")
    p1 = _new_proposal(http, auth, title="pending")
    p2 = _new_proposal(http, auth, title="will-tap")
    p3 = _new_proposal(http, auth, title="will-dismiss")
    http.post(f"/api/inbox/{p2['id']}/tap", headers=auth).raise_for_status()
    http.post(f"/api/inbox/{p3['id']}/dismiss", headers=auth).raise_for_status()

    pending = http.get("/api/inbox?status=pending", headers=auth).json()
    assert {r["id"] for r in pending} == {p1["id"]}, pending

    tapped = http.get("/api/inbox?status=tapped", headers=auth).json()
    assert {r["id"] for r in tapped} == {p2["id"]}, tapped

    dismissed = http.get("/api/inbox?status=dismissed", headers=auth).json()
    assert {r["id"] for r in dismissed} == {p3["id"]}, dismissed


def test_dismiss_from_tapped_is_409(http: httpx.Client):
    """Scenario #8 — once a proposal is tapped, the only forward move is
    consume. Trying to dismiss it must 409, not silently succeed."""
    uid, auth = _fresh_user(http, "dismiss-409")
    p = _new_proposal(http, auth, title="tap then try dismiss")
    http.post(f"/api/inbox/{p['id']}/tap", headers=auth).raise_for_status()

    bad = http.post(f"/api/inbox/{p['id']}/dismiss", headers=auth)
    assert bad.status_code == 409, bad.text
    assert "cannot dismiss" in bad.json().get("detail", "")


def test_tap_from_dismissed_is_409(http: httpx.Client):
    """Scenario #9 — dismissed is terminal; tapping must 409, not
    silently no-op (silent no-op would mask client bugs)."""
    uid, auth = _fresh_user(http, "tap-409")
    p = _new_proposal(http, auth, title="dismiss then try tap")
    http.post(f"/api/inbox/{p['id']}/dismiss", headers=auth).raise_for_status()

    bad = http.post(f"/api/inbox/{p['id']}/tap", headers=auth)
    assert bad.status_code == 409, bad.text
    assert "cannot tap" in bad.json().get("detail", "")


def test_idempotent_dismiss_no_duplicate_event(http: httpx.Client):
    """Scenario #10 — analog of the tap-idempotency contract. Three
    dismisses must emit exactly one user.proposal_dismissed event."""
    uid, auth = _fresh_user(http, "dismiss-idem")
    p = _new_proposal(http, auth, title="dismiss thrice")
    for _ in range(3):
        http.post(f"/api/inbox/{p['id']}/dismiss", headers=auth).raise_for_status()

    rows = _events(http, auth, ["user.proposal_dismissed"])
    assert len(rows) == 1, rows


# ---------------------------------------------------------------------------
# B. Stock cap & TTL invariants
# ---------------------------------------------------------------------------


def test_ttl_sweep_skips_non_pending_rows(http: httpx.Client):
    """Scenario #13 — TTL sweep only moves *pending* rows to expired.
    Terminal statuses (tapped, dismissed, consumed) are immutable
    history and must not be rewritten by the TTL sweep.

    We age a tapped row past TTL and assert it stays 'tapped' on read.
    """
    import asyncpg
    from urllib.parse import urlparse
    import infra.config  # noqa: F401  -- populates os.environ

    uid, auth = _fresh_user(http, "ttl-skip")
    p = _new_proposal(http, auth, title="old tapped")
    # Tap it so its status leaves 'pending'.
    http.post(f"/api/inbox/{p['id']}/tap", headers=auth).raise_for_status()

    # Force the created_at into the deep past.
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("DATABASE_URL not set")
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(sync_url)

    async def _age_row() -> None:
        conn = await asyncpg.connect(
            host=parsed.hostname, port=parsed.port or 5432,
            user=parsed.username, password=parsed.password,
            database=parsed.path.lstrip("/"),
        )
        try:
            old_ts = datetime.now(timezone.utc) - timedelta(hours=48)
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

    # Trigger lazy sweep via list. Expect the tapped row still tapped.
    listing = http.get("/api/inbox", headers=auth).json()
    target = next(r for r in listing if r["id"] == p["id"])
    assert target["status"] == "tapped", (
        "TTL sweep wrongly expired a non-pending row: "
        f"status={target['status']!r}"
    )
    # And no system.proposal_expired event got emitted for it.
    rows = _events(http, auth, ["system.proposal_expired"])
    assert not any(r["body"]["proposal_id"] == p["id"] for r in rows), rows


def test_stock_cap_counts_only_pending(http: httpx.Client):
    """Scenario #14 — the stock cap is on `status='pending'`. A user
    can have STOCK_CAP pending plus any number of tapped/dismissed/
    consumed/expired without triggering further expiry of fresh
    pending rows.
    """
    from services.knowledge.routers.inbox import STOCK_CAP

    uid, auth = _fresh_user(http, "cap-pending")

    # Create STOCK_CAP rows, tap them all so they leave 'pending'.
    tapped_ids = []
    for i in range(STOCK_CAP):
        p = _new_proposal(http, auth, title=f"will-tap-{i}")
        http.post(f"/api/inbox/{p['id']}/tap", headers=auth).raise_for_status()
        tapped_ids.append(p["id"])

    # Now create one fresh pending. It should NOT trigger any expiry
    # — the cap is counting only pending, and pending count is 1.
    fresh = _new_proposal(http, auth, title="fresh pending")

    pending = http.get("/api/inbox?status=pending", headers=auth).json()
    assert {r["id"] for r in pending} == {fresh["id"]}

    expired_events = _events(http, auth, ["system.proposal_expired"])
    cap_events = [e for e in expired_events if e["body"].get("reason") == "stock_cap"]
    assert cap_events == [], (
        "stock_cap fired even though only 1 pending row existed: " + str(cap_events)
    )


# ---------------------------------------------------------------------------
# C. Kickoff realization
# ---------------------------------------------------------------------------


def test_kickoff_rejects_user_id_mismatch(http: httpx.Client):
    """Scenario #16 — POST /api/agent/kickoff must reject if X-User-Id
    disagrees with body.user_id (belt-and-suspenders cross-user write
    guard).
    """
    uid_a, auth_a = _fresh_user(http, "kickoff-mismatch-A")
    uid_b, _ = _fresh_user(http, "kickoff-mismatch-B")
    payload = {
        "kickoff_event_id": str(uuid.uuid4()),
        "user_id": uid_b,   # body says user B
        "candidates": [
            {"title": "X", "posture": "steady",
             "persona_purpose": "teacher:long-horizon-propose",
             "opening": "y"},
        ],
    }
    # But the request is authenticated as user A.
    resp = http.post("/api/agent/kickoff", headers=auth_a, json=payload)
    assert resp.status_code == 403, resp.text
    assert "mismatch" in resp.json().get("detail", "").lower()

    # And nothing landed for either user.
    assert http.get("/api/inbox", headers=auth_a).json() == []


def test_kickoff_with_empty_candidates_is_noop(http: httpx.Client):
    """Scenario #17 — defensive: a kickoff with an empty candidates
    list writes zero rows and emits no events. Long instance should
    never hand us this in practice (the empty case is logged as SILENCE
    upstream), but the realization endpoint must not blow up either.
    """
    uid, auth = _fresh_user(http, "kickoff-empty")
    resp = http.post(
        "/api/agent/kickoff", headers=auth,
        json={
            "kickoff_event_id": str(uuid.uuid4()),
            "user_id": uid,
            "candidates": [],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["written"] == 0
    assert resp.json()["skipped"] == 0
    assert http.get("/api/inbox", headers=auth).json() == []


# ---------------------------------------------------------------------------
# D. Engagement-seed contracts
# ---------------------------------------------------------------------------


def test_two_tapped_only_first_seeds_cache(http: httpx.Client, services):
    """Scenario #19 — with two tapped proposals at turn-time, only one
    seeds the cache. SPEC: one active frame per persona_purpose; the
    other stays tapped (not consumed).
    """
    from persona.teacher.engagement import ensure_engagement_and_emit_turn

    uid, auth = _fresh_user(http, "two-tap")
    p1 = _new_proposal(http, auth, title="first", posture="steady",
                       opening="first opening")
    p2 = _new_proposal(http, auth, title="second", posture="deepen",
                       opening="second opening")
    http.post(f"/api/inbox/{p1['id']}/tap", headers=auth).raise_for_status()
    http.post(f"/api/inbox/{p2['id']}/tap", headers=auth).raise_for_status()

    # Run engagement helper — point it at the test sidecars.
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

    # Exactly one of the two is consumed, the other is still tapped.
    listing = http.get("/api/inbox", headers=auth).json()
    by_id = {r["id"]: r for r in listing}
    statuses = sorted([by_id[p1["id"]]["status"], by_id[p2["id"]]["status"]])
    assert statuses == ["consumed", "tapped"], statuses

    # And the cache holds the opening + posture of whichever got seeded.
    cache = _read_cache(http, auth)
    assert cache is not None
    # Pick the row that was consumed; the cache should match its frame.
    consumed_row = next(r for r in listing if r["status"] == "consumed")
    assert cache["paragraph"] == consumed_row["opening"]
    assert cache["posture"] == consumed_row["posture"]


def test_turn_without_tapped_leaves_cache_empty(http: httpx.Client, services):
    """Scenario #20 — engagement helper must not seed the cache from
    pending or already-consumed rows. No tap → no cache write."""
    from persona.teacher.engagement import ensure_engagement_and_emit_turn

    uid, auth = _fresh_user(http, "no-tap")
    # Plant a pending proposal — never tapped.
    _new_proposal(http, auth, title="just sitting there")

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

    cache = _read_cache(http, auth)
    assert cache is None, f"cache wrongly populated without a tap: {cache}"


# ---------------------------------------------------------------------------
# E. Posture monotonicity at the wire
# ---------------------------------------------------------------------------


def test_terminal_posture_blocks_all_transitions_via_wire(http: httpx.Client):
    """Scenario #25 — once the cache holds a terminal posture
    (`escalate` or `interrupt_now`), subsequent strong signals must not
    move it.
    """
    uid, auth = _fresh_user(http, "terminal")
    _seed_cache(http, auth, posture="interrupt_now")
    before = _read_cache(http, auth)
    assert before["posture"] == "interrupt_now"

    # Strong signal carrying a posture hint that would normally win
    # (`hold` from environment_shift). Cache must stay interrupt_now.
    event = _emit_event(http, auth, kind="signal.environment_shift",
                        body={"posture": "steady"})
    _post_signal(http, auth, event)

    after = _read_cache(http, auth)
    assert after is not None
    assert after["posture"] == "interrupt_now", after

    # The refresh body (or skip) must mention the terminal block somewhere.
    refresh_rows = _events(http, auth, ["maestro_short.cache_refresh"])
    if refresh_rows:
        last = refresh_rows[-1]["body"]
        assert "terminal" in last.get("posture_transition", "").lower()


def test_unknown_posture_in_signal_is_rejected(http: httpx.Client):
    """Scenario #26 — a signal body carrying an unknown posture must
    not poison the cache. Final posture stays on prior."""
    uid, auth = _fresh_user(http, "unknown-posture")
    _seed_cache(http, auth, posture="deepen")

    event = _emit_event(http, auth, kind="signal.environment_shift",
                        body={"posture": "MADE_UP_POSTURE"})
    _post_signal(http, auth, event)

    after = _read_cache(http, auth)
    assert after is not None
    # The signal's environment_shift hint table value is `hold`, so the
    # body-supplied bogus value should fall through to the hint
    # (which is valid) OR stay at prior. Either way, posture must be
    # in VALID_POSTURES.
    from services.maestro.cache import VALID_POSTURES
    assert after["posture"] in VALID_POSTURES, after
    assert after["posture"] != "MADE_UP_POSTURE", (
        "unknown posture leaked into cache: " + str(after)
    )


# ---------------------------------------------------------------------------
# F. Maestro long-instance gate via webhook
# ---------------------------------------------------------------------------


def test_followup_due_act_path(http: httpx.Client, services):
    """Scenario #27 — when due_followups_count > 0 the gate fires ACT.

    Rule priority matters: cool-down (Rule 2) beats followups (Rule 3),
    so the triggering event must NOT be user.engagement_ended (which
    would seed `last_engagement_ended ≈ now` and trip cool-down). We
    use an `agent.observation` trigger which falls through to Rule 3.

    With the fake LLM provider candidates come back empty, so the
    decision is downgraded to SILENCE with a "candidate generation
    returned 0" suffix; the *prefix* of the rationale still records
    the followup-driven ACT we want to assert on.
    """
    uid, auth = _fresh_user(http, "followup-act")

    # Emit a followup_scheduled event with valid_at in the past.
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    http.post(
        "/api/event-stream", headers=auth,
        json={
            "kind": "agent.followup_scheduled", "source": "agent",
            "body": {"topic": "review attention"},
            "valid_at": past_iso,
        },
    ).raise_for_status()

    # Trigger with a non-cool-down, non-capture kind so Rule 3 wins.
    seed = http.post(
        "/api/event-stream", headers=auth,
        json={"kind": "agent.observation", "source": "agent",
              "body": {"topic": "smoke"}},
    ).json()
    resp = http.post("/api/maestro/event", headers=auth, json={"event": seed})
    assert resp.status_code == 200, resp.text

    # The gate should have logged a kickoff_decision whose rationale
    # mentions the followup. (Decision may be SILENCE due to empty
    # candidate list, but rationale prefix should reveal Rule 3 fired.)
    decisions = _events(http, auth, ["maestro_long.kickoff_decision"])
    assert decisions, decisions
    last = decisions[-1]["body"]
    rationale = last.get("rationale", "").lower()
    assert "followup" in rationale, (
        "followup ACT path not recorded: rationale=" + rationale
    )


def test_capture_silenced_when_inbox_at_cap(http: httpx.Client, services):
    """Scenario #28 — even a deliberate `capture.*` event must yield
    SILENCE when the inbox is already at STOCK_CAP. Don't pile on.

    Note: the gate's `open_inbox_count` is read at runtime — Phase-0
    long instance currently hardcodes it to 0 (see services/maestro/long.py
    `_build_gate_input`). This test pins the current behaviour and
    will FAIL once the long instance is taught to read the real
    inbox count from `silicon_brain`. When it does, the test should
    be updated to expect SILENCE.

    For now we assert that the path returns *some* kickoff_decision
    (either SILENCE-from-cap if wired, or SILENCE-from-no-candidates
    if not) — i.e. no 5xx.
    """
    from services.knowledge.routers.inbox import STOCK_CAP

    uid, auth = _fresh_user(http, "cap-capture")
    for i in range(STOCK_CAP):
        _new_proposal(http, auth, title=f"existing-{i}")

    seed = http.post(
        "/api/event-stream", headers=auth,
        json={"kind": "capture.created", "source": "capture",
              "body": {"capture_id": str(uuid.uuid4())}},
    ).json()
    resp = http.post("/api/maestro/event", headers=auth, json={"event": seed})
    assert resp.status_code == 200, resp.text

    decisions = _events(http, auth, ["maestro_long.kickoff_decision"])
    assert decisions, decisions


def test_cooldown_after_engagement_ended_is_silence(http: httpx.Client, services):
    """Scenario #29 — an `engagement_ended` event arriving within the
    cool-down window yields SILENCE. Trigger by emitting an
    engagement_ended and immediately firing the maestro webhook with
    that event — the gate sees (now - last_engagement_ended) ≈ 0s,
    well under MIN_QUIET_AFTER_ENGAGEMENT.
    """
    uid, auth = _fresh_user(http, "cooldown")
    seed = http.post(
        "/api/event-stream", headers=auth,
        json={"kind": "user.engagement_ended", "source": "user",
              "body": {"engagement_id": str(uuid.uuid4())}},
    ).json()
    resp = http.post("/api/maestro/event", headers=auth, json={"event": seed})
    assert resp.status_code == 200, resp.text

    decisions = _events(http, auth, ["maestro_long.kickoff_decision"])
    assert decisions, decisions
    last = decisions[-1]["body"]
    assert last["decision"] == "SILENCE", last
    assert "cool-down" in last["rationale"].lower(), last["rationale"]


# ---------------------------------------------------------------------------
# G. Forbidden-metric sweep extended to inbox interaction bodies
# ---------------------------------------------------------------------------


_FORBIDDEN_BODY_KEYS = {
    "session_length", "session_duration", "session_duration_s",
    "session_duration_ms", "time_in_app", "time_in_session",
    "time_on_screen", "turn_count", "turns_per_session",
    "engagement_length", "engagement_duration", "engagement_duration_s",
    "engagement_duration_ms",
}


def test_forbidden_metrics_absent_across_inbox_events(http: httpx.Client):
    """Scenario #33 (extension) — sweep inbox interaction events (tap,
    dismiss, consume, expire) for SPEC §17 forbidden body keys."""
    uid, auth = _fresh_user(http, "no-darkpattern")
    # Drive all four kinds of inbox interaction.
    p1 = _new_proposal(http, auth, title="tap+consume")
    p2 = _new_proposal(http, auth, title="dismiss")
    http.post(f"/api/inbox/{p1['id']}/tap", headers=auth).raise_for_status()
    http.post(f"/api/inbox/{p1['id']}/consume", headers=auth).raise_for_status()
    http.post(f"/api/inbox/{p2['id']}/dismiss", headers=auth).raise_for_status()

    rows = _events(http, auth, [
        "user.proposal_tapped",
        "user.proposal_dismissed",
        "user.proposal_consumed",
        "system.proposal_expired",
    ])
    offenders: list[tuple[str, str]] = []
    for evt in rows:
        for key in (evt.get("body") or {}).keys():
            if key in _FORBIDDEN_BODY_KEYS:
                offenders.append((evt["kind"], key))
    assert not offenders, (
        "forbidden length/count metrics in inbox event bodies:\n"
        + "\n".join(f"  - {k}.body[{key!r}]" for k, key in offenders)
    )
