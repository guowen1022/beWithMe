"""E2E for the Phase-0 projections (PR-1).

PR-1 ships one fully-implemented projection (`current_engagement_state`)
plus six stubs. The stubs round-trip a `{"_stub": True, "name": ...}`
shape so consumers can wire against the contract before later PRs land.
"""
from __future__ import annotations

import time

import httpx
import pytest

from silicon_brain.projections import PROJECTIONS, projection_names


def _emit(http: httpx.Client, auth: dict[str, str], kind: str, **body) -> dict:
    resp = http.post(
        "/api/event-stream",
        headers=auth,
        json={"kind": kind, "source": "user", "body": body},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _read(http: httpx.Client, auth: dict[str, str], name: str) -> dict:
    resp = http.get(f"/api/event-stream/projections/{name}", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_unknown_projection_404(http: httpx.Client, auth: dict[str, str]):
    resp = http.get("/api/event-stream/projections/not_a_real_projection", headers=auth)
    assert resp.status_code == 404
    detail = resp.json().get("detail", "")
    assert "unknown projection" in detail
    # Lists the known set so callers can recover programmatically.
    for name in projection_names():
        assert name in detail


def test_projection_requires_user_id(http: httpx.Client):
    resp = http.get("/api/event-stream/projections/current_engagement_state")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "stub_name",
    [n for n in PROJECTIONS if n != "current_engagement_state"],
)
def test_stub_projections_respond_with_marker(
    http: httpx.Client, auth: dict[str, str], stub_name: str
):
    body = _read(http, auth, stub_name)
    assert body == {"_stub": True, "name": stub_name}


def test_engagement_state_transitions(http: httpx.Client, auth: dict[str, str]):
    """started → active; ended → idle with last_engagement summary.

    The projection looks at the *latest* engagement_started/ended for this
    user across all history. To make the assertion robust against other
    tests in the session, we drive the transition by always emitting the
    `engagement_ended` last and reading immediately.
    """
    engagement_id = f"e2e-proj-{int(time.time() * 1000)}"

    # Active: started is the newest of the two.
    _emit(
        http, auth,
        "user.engagement_started",
        engagement_id=engagement_id,
    )
    active = _read(http, auth, "current_engagement_state")
    assert active["status"] == "active"
    assert active["engagement_id"] == engagement_id
    assert "started_at" in active

    # Idle: now ended is the newest.
    _emit(
        http, auth,
        "user.engagement_ended",
        engagement_id=engagement_id,
    )
    idle = _read(http, auth, "current_engagement_state")
    assert idle["status"] == "idle"
    assert idle["last_engagement"]["engagement_id"] == engagement_id
    assert "ended_at" in idle["last_engagement"]


def test_engagement_state_idle_for_fresh_user(http: httpx.Client):
    """A brand-new user with zero events reads `{"status": "idle"}` flat.

    Different from the post-ended path: no `last_engagement` key because
    no engagement ever happened.
    """
    tag = int(time.time() * 1000)
    create_resp = http.post("/api/users", json={"username": f"e2e-proj-fresh-{tag}"})
    assert create_resp.status_code == 200, create_resp.text
    fresh_auth = {"X-User-Id": create_resp.json()["id"]}

    body = _read(http, fresh_auth, "current_engagement_state")
    assert body == {"status": "idle"}
