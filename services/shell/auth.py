"""Shell auth — the only place authentication lives.

Two modes, selected by `BEWITHME_AUTH_MODE` (see docs/SECURITY.md).

**legacy** (default) — historical behaviour, unchanged:

  1. Read `X-User-Id` from the request headers (401 if missing/malformed).
  2. Call knowledge `/api/auth/verify` once per user (cached for AUTH_TTL
     seconds) — knowledge owns the DB and is the source of truth.
  3. Forward the request only if verification succeeded.

  This verifies that a user *exists*, not that the caller *is* that user. The
  header is an unverified assertion, and `GET /api/users` publishes every id
  to assert. Safe on a private network; unsafe on a public address.
  ARCHITECTURE.md section 6 says exactly this.

**strict** — required for any internet-facing deployment:

  1. Identity comes only from a signed `Authorization: Bearer <token>`
     (infra/session_token.py). A client cannot mint one.
  2. Any client-supplied `X-User-Id` is discarded by the proxy before
     forwarding; the shell injects the id it derived from the token.
  3. Anonymous user enumeration and anonymous user creation are closed.

In both modes the DB check still runs, so deleting a user immediately
invalidates their access even if they hold an unexpired token.

Sidecars trust whatever `X-User-Id` the shell forwards. Direct (non-shell)
access bypasses auth in either mode — only expose sidecars on a private
network (ARCHITECTURE.md invariant 9).
"""
from __future__ import annotations

import time
from uuid import UUID

import httpx

from infra import session_token
from infra.config import settings
from infra.topology import upstream_url


# (method, path) tuples that bypass auth.
#
# legacy: the bootstrapping surface the frontend hits before a user is chosen.
# GET /api/users is what makes enumeration possible, which is why strict drops it.
PUBLIC_LEGACY: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/"),
    ("HEAD", "/"),
    ("GET", "/api/health"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
})

# strict: only liveness and the token exchange itself. `POST /api/auth/session`
# is unauthenticated by necessity — it is where a caller proves the access key
# — so it must be rate-limited (see services/shell/main.py).
PUBLIC_STRICT: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/"),
    ("HEAD", "/"),
    ("GET", "/api/health"),
    ("POST", "/api/auth/session"),
})

AUTH_TTL_SECONDS = 60.0


def auth_mode() -> str:
    """Active mode: "legacy" (default) or "strict"."""
    mode = (settings.bewithme_auth_mode or "legacy").strip().lower()
    return mode if mode in {"legacy", "strict"} else "legacy"


def is_strict() -> bool:
    return auth_mode() == "strict"


def startup_check() -> list[str]:
    """Configuration problems that make the current mode unsafe or unusable.

    Returned as strings so the caller decides whether to warn or refuse to
    boot. Empty list means the configuration is coherent.
    """
    problems: list[str] = []
    if is_strict():
        if not session_token.is_configured():
            problems.append(
                "BEWITHME_AUTH_MODE=strict requires BEWITHME_SECRET_KEY (>= 16 chars). "
                "Generate: python -c \"from infra.session_token import generate_secret_key as g; print(g())\""
            )
        if not (settings.bewithme_access_key or "").strip():
            problems.append(
                "BEWITHME_AUTH_MODE=strict requires BEWITHME_ACCESS_KEY — "
                "without it no session token can ever be issued."
            )
    return problems


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
    public = PUBLIC_STRICT if is_strict() else PUBLIC_LEGACY
    if (method.upper(), path) in public:
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


def resolve_identity(headers) -> str | None:
    """Derive the caller's user id from the request headers.

    strict: only a valid signed bearer token counts. `X-User-Id` is ignored
            entirely, so sending one buys an attacker nothing.
    legacy: the historical `X-User-Id` path.

    `headers` is any mapping with a case-insensitive `.get` (Starlette's
    `request.headers` qualifies).
    """
    if is_strict():
        token = session_token.bearer_from_header(headers.get("authorization"))
        uid = session_token.verify(token)
        # Normalise through the same UUID parse so downstream sees one shape.
        return parse_user_header(uid)
    return parse_user_header(headers.get("x-user-id"))


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
