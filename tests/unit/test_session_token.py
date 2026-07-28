"""Tests for infra/session_token.py — the signed-identity primitive.

These are the tests that matter most in the repo: if any of them regress, an
attacker can mint an identity and the whole auth model collapses.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

from infra import session_token
from infra.config import settings


KEY = "test-key-that-is-definitely-long-enough"
UID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def signed(monkeypatch):
    """Configure a signing key for the duration of one test."""
    monkeypatch.setattr(settings, "bewithme_secret_key", KEY, raising=False)
    return KEY


@pytest.fixture
def unsigned(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_secret_key", "", raising=False)


# ---------------------------------------------------------------- happy path


def test_roundtrip_returns_the_user_id(signed):
    assert session_token.verify(session_token.issue(UID)) == UID


def test_token_has_the_documented_three_part_shape(signed):
    parts = session_token.issue(UID).split(".")
    assert len(parts) == 3
    assert parts[0] == session_token.TOKEN_VERSION


def test_payload_carries_uid_iat_and_exp(signed):
    _, payload_b64, _ = session_token.issue(UID, ttl_seconds=123).split(".")
    pad = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    assert payload["uid"] == UID
    assert payload["exp"] - payload["iat"] == 123


# ------------------------------------------------------------------ forgery


def test_tampered_payload_is_rejected(signed):
    version, payload_b64, sig = session_token.issue(UID).split(".")
    forged_payload = {"uid": "99999999-9999-9999-9999-999999999999",
                      "iat": int(time.time()), "exp": int(time.time()) + 600}
    forged = base64.urlsafe_b64encode(
        json.dumps(forged_payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    # Swapping the payload while keeping the old signature must not verify.
    assert session_token.verify(f"{version}.{forged}.{sig}") is None


def test_tampered_signature_is_rejected(signed):
    version, payload_b64, sig = session_token.issue(UID).split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert session_token.verify(f"{version}.{payload_b64}.{flipped}") is None


def test_token_signed_with_another_key_is_rejected(signed, monkeypatch):
    token = session_token.issue(UID)
    monkeypatch.setattr(settings, "bewithme_secret_key", "a-completely-different-key", raising=False)
    assert session_token.verify(token) is None


def test_unsigned_payload_only_token_is_rejected(signed):
    """A caller cannot drop the signature and be believed."""
    _, payload_b64, _ = session_token.issue(UID).split(".")
    assert session_token.verify(f"{session_token.TOKEN_VERSION}.{payload_b64}.") is None
    assert session_token.verify(f"{session_token.TOKEN_VERSION}.{payload_b64}") is None


def test_version_swap_is_rejected(signed):
    _, payload_b64, sig = session_token.issue(UID).split(".")
    assert session_token.verify(f"v2.{payload_b64}.{sig}") is None


# ------------------------------------------------------------------ expiry


def test_expired_token_is_rejected(signed):
    assert session_token.verify(session_token.issue(UID, ttl_seconds=-1)) is None


def test_token_valid_just_before_expiry(signed):
    assert session_token.verify(session_token.issue(UID, ttl_seconds=60)) == UID


# ------------------------------------------------------- misconfiguration


def test_issue_refuses_without_a_key(unsigned):
    with pytest.raises(session_token.TokenError):
        session_token.issue(UID)


def test_verify_returns_none_without_a_key(signed, monkeypatch):
    token = session_token.issue(UID)
    monkeypatch.setattr(settings, "bewithme_secret_key", "", raising=False)
    assert session_token.verify(token) is None


def test_short_key_counts_as_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "bewithme_secret_key", "tooshort", raising=False)
    assert session_token.is_configured() is False


def test_generated_key_is_usable():
    assert len(session_token.generate_secret_key()) >= 16


# ------------------------------------------------------------ malformed input


@pytest.mark.parametrize(
    "bad",
    [None, "", "garbage", "a.b", "a.b.c.d", "v1..", "v1.!!!.!!!"],
)
def test_malformed_tokens_return_none(signed, bad):
    assert session_token.verify(bad) is None


def test_oversized_token_is_rejected_early(signed):
    assert session_token.verify("v1." + "A" * 10000 + ".sig") is None


# ------------------------------------------------------------ header parsing


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER  abc  ", "abc"),
        ("Basic abc", None),
        ("abc", None),
        ("Bearer", None),
        ("Bearer   ", None),
        (None, None),
        ("", None),
    ],
)
def test_bearer_header_parsing(header, expected):
    assert session_token.bearer_from_header(header) == expected
