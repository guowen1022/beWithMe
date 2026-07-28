"""Tests for the shell auth gate — legacy vs strict, and header sanitation.

The header-sanitation tests are the important ones. Sidecars trust
`X-User-Id` unconditionally (ARCHITECTURE.md invariant 9), so if the proxy ever
forwards a client's own value, every sidecar is impersonatable in one request.
"""
from __future__ import annotations

import pytest

from infra import session_token
from infra.config import settings
from services.shell import auth
from services.shell.main import _sanitize_client_headers

KEY = "test-key-that-is-definitely-long-enough"
ACCESS = "an-access-key"
UID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def legacy(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_auth_mode", "legacy", raising=False)


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_auth_mode", "strict", raising=False)
    monkeypatch.setattr(settings, "bewithme_secret_key", KEY, raising=False)
    monkeypatch.setattr(settings, "bewithme_access_key", ACCESS, raising=False)


# ------------------------------------------------------------------- modes


def test_default_mode_is_legacy(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_auth_mode", "", raising=False)
    assert auth.auth_mode() == "legacy"
    assert auth.is_strict() is False


def test_unknown_mode_falls_back_to_legacy(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_auth_mode", "banana", raising=False)
    assert auth.auth_mode() == "legacy"


def test_strict_mode_is_recognised(strict):
    assert auth.is_strict() is True


# ------------------------------------------------------------ public paths


def test_legacy_exposes_user_list(legacy):
    """The historical behaviour — and precisely the enumeration hole."""
    assert auth.is_public("GET", "/api/users") is True


def test_strict_closes_user_enumeration(strict):
    assert auth.is_public("GET", "/api/users") is False


def test_strict_closes_anonymous_user_creation(strict):
    assert auth.is_public("POST", "/api/users") is False


def test_strict_keeps_token_exchange_public(strict):
    """It must be reachable without a token — it is how you get one."""
    assert auth.is_public("POST", "/api/auth/session") is True


@pytest.mark.parametrize("mode", ["legacy", "strict"])
def test_health_is_public_in_both_modes(monkeypatch, mode):
    monkeypatch.setattr(settings, "bewithme_auth_mode", mode, raising=False)
    assert auth.is_public("GET", "/api/health") is True


def test_skill_assets_stay_public(strict):
    assert auth.is_public("GET", "/api/skills/note.js") is True


def test_protected_path_is_not_public(strict):
    assert auth.is_public("POST", "/api/ask") is False


# -------------------------------------------------------- identity resolution


def test_legacy_accepts_the_header(legacy):
    assert auth.resolve_identity({"x-user-id": UID}) == UID


def test_legacy_rejects_a_malformed_header(legacy):
    assert auth.resolve_identity({"x-user-id": "not-a-uuid"}) is None


def test_strict_ignores_the_header_entirely(strict):
    """The core fix: asserting an identity buys nothing in strict mode."""
    assert auth.resolve_identity({"x-user-id": UID}) is None


def test_strict_accepts_a_valid_token(strict):
    token = session_token.issue(UID)
    assert auth.resolve_identity({"authorization": f"Bearer {token}"}) == UID


def test_strict_token_wins_over_a_conflicting_header(strict):
    """A forged X-User-Id cannot override the signed identity."""
    token = session_token.issue(UID)
    resolved = auth.resolve_identity(
        {"authorization": f"Bearer {token}", "x-user-id": OTHER}
    )
    assert resolved == UID


def test_strict_rejects_a_forged_token(strict):
    assert auth.resolve_identity({"authorization": "Bearer v1.aaa.bbb"}) is None


def test_strict_rejects_a_missing_token(strict):
    assert auth.resolve_identity({}) is None


# -------------------------------------------------------- startup validation


def test_legacy_needs_no_configuration(legacy):
    assert auth.startup_check() == []


def test_strict_requires_a_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_auth_mode", "strict", raising=False)
    monkeypatch.setattr(settings, "bewithme_secret_key", "", raising=False)
    monkeypatch.setattr(settings, "bewithme_access_key", ACCESS, raising=False)
    assert any("BEWITHME_SECRET_KEY" in p for p in auth.startup_check())


def test_strict_requires_an_access_key(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_auth_mode", "strict", raising=False)
    monkeypatch.setattr(settings, "bewithme_secret_key", KEY, raising=False)
    monkeypatch.setattr(settings, "bewithme_access_key", "", raising=False)
    assert any("BEWITHME_ACCESS_KEY" in p for p in auth.startup_check())


def test_fully_configured_strict_mode_is_clean(strict):
    assert auth.startup_check() == []


# ------------------------------------------------------- header sanitation


def test_client_supplied_user_id_is_never_forwarded():
    """Without this, one header impersonates anyone on every sidecar."""
    out = _sanitize_client_headers({"X-User-Id": OTHER, "Accept": "application/json"})
    assert "x-user-id" not in {k.lower() for k in out}
    assert out["Accept"] == "application/json"


def test_authorization_stops_at_the_shell():
    out = _sanitize_client_headers({"Authorization": "Bearer secret"})
    assert "authorization" not in {k.lower() for k in out}


def test_sanitation_is_case_insensitive():
    out = _sanitize_client_headers({"x-USER-id": OTHER, "AUTHORIZATION": "Bearer x"})
    assert out == {}


def test_hop_by_hop_headers_are_dropped():
    out = _sanitize_client_headers({"Connection": "keep-alive", "Host": "evil"})
    assert out == {}


def test_ordinary_headers_survive():
    out = _sanitize_client_headers({"Content-Type": "application/json", "X-Trace": "1"})
    assert out == {"Content-Type": "application/json", "X-Trace": "1"}
