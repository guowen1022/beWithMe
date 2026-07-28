"""Signed session tokens -- makes an identity claim unforgeable.

The problem this solves (ARCHITECTURE.md section 6): `X-User-Id` is an *assertion*
of identity, not proof of one. Anything that can set a header can be any user.
A token signed with a server-side key cannot be produced by a client, so the
shell can trust the identity inside it.

Format (opaque to clients, three dot-separated parts):

    v1.<base64url(payload)>.<base64url(hmac-sha256)>

The payload is JSON `{"uid": ..., "iat": ..., "exp": ...}`. The signature covers
`v1.<payload>` so neither the version nor the payload can be swapped.

Deliberately stdlib-only (`hmac`, `hashlib`, `secrets`, `base64`, `json`): this
is the authentication path, and adding a JWT dependency here would widen the
attack surface for a format we do not need. No algorithm is negotiated, so the
`alg: none` class of JWT bug cannot exist.

infra is the leaf layer -- this module imports nothing above it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Final

from infra.config import settings


TOKEN_VERSION: Final[str] = "v1"

# 30 days. Long enough that a personal-assistant client never visibly
# re-authenticates; short enough that a leaked token eventually dies.
DEFAULT_TTL_SECONDS: Final[int] = 30 * 24 * 60 * 60

# Reject absurdly large inputs before doing any parsing work.
_MAX_TOKEN_BYTES: Final[int] = 4096


class TokenError(RuntimeError):
    """Raised when a token cannot be issued (never when one fails to verify)."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    # base64url without padding -- restore it before decoding.
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def signing_key() -> bytes:
    """The HMAC key, or empty bytes when unconfigured.

    Callers that require signing must check `is_configured()` first and fail
    loudly at startup rather than silently issuing unverifiable tokens.
    """
    return (settings.bewithme_secret_key or "").encode("utf-8")


def is_configured() -> bool:
    """True when BEWITHME_SECRET_KEY is set to a usable value."""
    return len(signing_key()) >= 16


def generate_secret_key() -> str:
    """A fresh key suitable for BEWITHME_SECRET_KEY (for scripts/docs)."""
    return secrets.token_urlsafe(48)


def _sign(signing_input: str) -> str:
    return _b64e(hmac.new(signing_key(), signing_input.encode("ascii"), hashlib.sha256).digest())


def issue(user_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed token for `user_id`.

    Raises TokenError when no signing key is configured -- issuing a token that
    cannot be verified would be worse than refusing.
    """
    if not is_configured():
        raise TokenError(
            "BEWITHME_SECRET_KEY is unset or too short (need >= 16 chars). "
            "Generate one with: python -c "
            "'from infra.session_token import generate_secret_key; print(generate_secret_key())'"
        )

    now = int(time.time())
    payload = {"uid": str(user_id), "iat": now, "exp": now + int(ttl_seconds)}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{TOKEN_VERSION}.{payload_b64}"
    return f"{signing_input}.{_sign(signing_input)}"


def verify(token: str | None) -> str | None:
    """Return the user id inside a valid token, else None.

    Never raises and never distinguishes *why* a token is bad -- a caller that
    reported "bad signature" vs "expired" would leak information. Every failure
    path returns None.
    """
    if not token or not is_configured():
        return None
    if len(token) > _MAX_TOKEN_BYTES:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None
    version, payload_b64, sig = parts
    if version != TOKEN_VERSION:
        return None

    expected = _sign(f"{version}.{payload_b64}")
    # Constant-time: a byte-by-byte early exit would leak the signature.
    if not hmac.compare_digest(expected, sig):
        return None

    try:
        payload = json.loads(_b64d(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    uid = payload.get("uid")
    exp = payload.get("exp")
    if not isinstance(uid, str) or not isinstance(exp, int):
        return None
    if exp < int(time.time()):
        return None

    return uid


def bearer_from_header(value: str | None) -> str | None:
    """Extract the token from an `Authorization: Bearer <token>` header."""
    if not value:
        return None
    prefix = "bearer "
    if value[: len(prefix)].lower() != prefix:
        return None
    token = value[len(prefix):].strip()
    return token or None
