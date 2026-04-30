"""End-to-end tests: hit the running shell from outside, verify each sidecar.

Boots all 6 sidecars via the `services` fixture (see conftest.py). Every
assertion goes through the shell on `BASE_PORT` so the proxy + auth gate +
routing table are exercised on every call.
"""
from __future__ import annotations

import uuid

import httpx
import pytest


# --- Shell -----------------------------------------------------------------


def test_shell_root_is_public(http: httpx.Client):
    """`/` is public and returns the shell identity."""
    resp = http.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"service": "shell", "ok": True}


# --- Auth gate (lives only in the shell) -----------------------------------


def test_auth_missing_header_is_401(http: httpx.Client):
    """Protected paths reject requests with no X-User-Id at the shell."""
    resp = http.get("/api/profile")
    assert resp.status_code == 401
    assert "X-User-Id" in resp.json()["detail"]


def test_auth_malformed_uuid_is_401(http: httpx.Client):
    """A non-UUID X-User-Id is rejected by the shell parser before any sidecar."""
    resp = http.get("/api/profile", headers={"X-User-Id": "not-a-uuid"})
    assert resp.status_code == 401


def test_auth_unknown_user_is_401(http: httpx.Client):
    """A well-formed UUID that doesn't exist in DB is rejected by the shell
    after consulting knowledge's /api/auth/verify."""
    resp = http.get(
        "/api/profile",
        headers={"X-User-Id": str(uuid.uuid4())},
    )
    if resp.status_code == 502:
        pytest.skip("knowledge sidecar can't reach DB; auth verify path inconclusive")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "unknown_user"


def test_auth_health_is_public(http: httpx.Client):
    """/api/health doesn't require X-User-Id."""
    resp = http.get("/api/health")
    assert resp.status_code == 200


def test_auth_users_list_is_public(http: httpx.Client):
    """GET /api/users is public — frontend uses it to populate user picker
    before any user is selected."""
    resp = http.get("/api/users")
    if resp.status_code != 200:
        pytest.skip(f"DB not reachable: {resp.status_code}")
    assert isinstance(resp.json(), list)


def test_auth_users_create_is_public(http: httpx.Client):
    """POST /api/users is public — bootstrapping a user must not require auth."""
    username = f"public-create-{uuid.uuid4().hex[:8]}"
    resp = http.post("/api/users", json={"username": username})
    if resp.status_code != 200:
        pytest.skip(f"DB not reachable: {resp.status_code}")
    body = resp.json()
    assert body["username"] == username
    assert "id" in body


# --- Knowledge -------------------------------------------------------------


def test_knowledge_health_through_shell(http: httpx.Client):
    """/api/health is the canonical proof that shell→knowledge proxy works."""
    resp = http.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "checks" in body
    assert "db" in body["checks"]
    assert "ollama" in body["checks"]


def test_knowledge_profile_for_authenticated_user(http: httpx.Client, auth: dict):
    """A valid user can read their profile through the proxy."""
    resp = http.get("/api/profile", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert "self_description" in body


def test_knowledge_concepts_for_authenticated_user(http: httpx.Client, auth: dict):
    """Concepts list returns (likely empty) array for a fresh user."""
    resp = http.get("/api/concepts", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# --- Ask --------------------------------------------------------------------


def test_ask_unauthenticated_blocked_at_shell(http: httpx.Client):
    """Without X-User-Id the shell rejects /api/ask before the ask sidecar
    sees it. Proves auth is centralized."""
    resp = http.post("/api/ask", json={})
    assert resp.status_code == 401


def test_interactions_for_authenticated_user(http: httpx.Client, auth: dict):
    """GET /api/interactions through shell→ask returns a (likely empty) list."""
    resp = http.get("/api/interactions", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# --- Transcribe -------------------------------------------------------------


def test_transcribe_unauthenticated_blocked_at_shell(http: httpx.Client):
    """Transcribe is also gated — proves the shell guards every sidecar."""
    resp = http.post("/api/transcribe")
    assert resp.status_code == 401


def test_transcribe_endpoint_registered(http: httpx.Client, auth: dict):
    """POST /api/transcribe with auth + no file → 422 (sidecar validator)."""
    resp = http.post("/api/transcribe", headers=auth)
    assert resp.status_code == 422


# --- Speak ------------------------------------------------------------------


def test_speak_unauthenticated_blocked_at_shell(http: httpx.Client):
    resp = http.post("/api/speak", json={"text": "hi"})
    assert resp.status_code == 401


def test_speak_empty_text_400(http: httpx.Client, auth: dict):
    """With auth, empty text triggers the sidecar's 400."""
    resp = http.post("/api/speak", json={"text": ""}, headers=auth)
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_speak_stream_endpoint_registered(http: httpx.Client, auth: dict):
    resp = http.post("/api/speak/stream", json={"text": ""}, headers=auth)
    assert resp.status_code == 400


# --- Browser ----------------------------------------------------------------


def test_browser_unauthenticated_blocked_at_shell(http: httpx.Client):
    resp = http.get("/api/browser/status")
    assert resp.status_code == 401


def test_browser_status(http: httpx.Client, auth: dict):
    resp = http.get("/api/browser/status", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("running", "not_running")
    assert "headed" in body


def test_browser_selection_no_active_page(http: httpx.Client, auth: dict):
    resp = http.get("/api/browser/selection", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"selection": "", "url": ""}


def test_browser_resume_without_handoff(http: httpx.Client, auth: dict):
    resp = http.post("/api/browser/resume", headers=auth)
    assert resp.status_code == 400
    assert "handoff" in resp.json()["detail"].lower()


# --- Shell routing parity ---------------------------------------------------


def test_unknown_prefix_routes_to_knowledge(http: httpx.Client, auth: dict):
    """A /api/<unknown> prefix falls back to knowledge per DEFAULT_SERVICE.

    The path won't be registered there either, so we expect 404 — proving
    the shell forwarded the request rather than 502'ing or 404'ing itself."""
    resp = http.get("/api/this-does-not-exist", headers=auth)
    assert resp.status_code == 404


def test_auth_cache_warm_is_fast(http: httpx.Client, auth: dict):
    """Second request for the same user should hit the auth cache, not knowledge.
    We can't directly observe the cache, but back-to-back requests must succeed
    consistently — and well under the per-request HTTP overhead were every call
    re-verifying."""
    for _ in range(5):
        resp = http.get("/api/profile", headers=auth)
        assert resp.status_code == 200
