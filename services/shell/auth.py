"""Shell auth — the only place authentication lives.

For every protected request the shell:

  1. Reads `X-User-Id` from the request headers (401 if missing/malformed).
  2. Calls knowledge `/api/auth/verify` once per user (cached for AUTH_TTL
     seconds) — knowledge owns the DB and is the source of truth.
  3. Forwards the request only if verification succeeded.

Sidecars trust whatever `X-User-Id` the shell forwards. Direct (non-shell)
access bypasses auth — only expose sidecars on the same private network.

Public paths (no verification): /, /api/health, GET/POST /api/users.
"""
from __future__ import annotations

import time
from typing import Iterable
from uuid import UUID

import httpx

from infra.topology import upstream_url


# (method, path) tuples that bypass auth — covers the bootstrapping surface
# the frontend hits before any user is selected.
PUBLIC: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/"),
    ("HEAD", "/"),
    ("GET", "/api/health"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
})

AUTH_TTL_SECONDS = 60.0


class AuthCache:
    """Trivial TTL cache for verified user ids. Single-process, in-memory."""

    def __init__(self, ttl: float = AUTH_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._store: dict[str, float] = {}

    def hit(self, user_id: str) -> bool:
        expiry = self._store.get(user_id)
        if expiry is None:
            return False
        if expiry < time.monotonic():
            self._store.pop(user_id, None)
            return False
        return True

    def remember(self, user_id: str) -> None:
        self._store[user_id] = time.monotonic() + self._ttl

    def forget(self, user_id: str) -> None:
        self._store.pop(user_id, None)


def is_public(method: str, path: str) -> bool:
    if (method.upper(), path) in PUBLIC:
        return True
    # Skill JS files are static public assets — no auth required.
    if method.upper() == "GET" and path.startswith("/api/skills/"):
        return True
    return False


def parse_user_header(value: str | None) -> str | None:
    """Return a canonical UUID string if `value` is well-formed, else None."""
    if not value:
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        return None


async def verify_against_knowledge(
    client: httpx.AsyncClient,
    user_id: str,
    cache: AuthCache,
) -> bool:
    """Returns True if the user exists per knowledge's /api/auth/verify."""
    if cache.hit(user_id):
        return True
    try:
        resp = await client.get(
            f"{upstream_url('knowledge')}/api/auth/verify",
            headers={"X-User-Id": user_id},
            timeout=5.0,
        )
    except httpx.HTTPError:
        return False
    if resp.status_code == 200:
        cache.remember(user_id)
        return True
    return False
