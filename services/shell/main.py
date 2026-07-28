"""beWithMe API gateway — :BASE_PORT.

A thin reverse proxy. Holds no DB session, no models, no Playwright. Forwards
every `/api/<prefix>/...` request to the corresponding sidecar based on
`infra.topology.PREFIX_TO_SERVICE` and streams the response back.

It is also the only authentication point (services/shell/auth.py). Two rules
hold in every mode:

  * A client-supplied `X-User-Id` is **never** forwarded. The shell strips it
    and injects the id it derived itself. Sidecars trust that header
    (ARCHITECTURE.md invariant 9), so letting a client set it would hand them
    a forged identity.
  * Public paths are rate-limited, because they are reachable without credentials.

Run standalone (port computed from BASE_PORT env, default 8000):
    python -m services.shell
"""
from __future__ import annotations

import logging
import time
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services.shell.auth import (
    AuthCache,
    auth_mode,
    is_public,
    is_strict,
    resolve_identity,
    startup_check,
    verify_against_knowledge,
)
from infra.config import settings
from infra.event_log_middleware import install_event_log
from infra.topology import route_for_path, service_port, upstream_url


log = logging.getLogger("bewithme.shell")

# Hop-by-hop headers that must not be forwarded (RFC 7230 §6.1) plus a few
# that httpx / starlette recompute for us.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

# Headers a client must never be able to set on an upstream request.
# `x-user-id` IS the identity for every sidecar, so accepting a client's value
# would be a straight impersonation primitive. `authorization` stops at the
# shell — it is consumed here and carries no meaning downstream.
_CLIENT_FORBIDDEN = {"x-user-id", "authorization"}

# Public endpoints are reachable with no credentials, so they get a cheap
# per-IP limit. Deliberately in-memory: the shell is one process, and a real
# deployment should also have a limit at the SLB/WAF.
_RATE_LIMIT_REQUESTS = 30
_RATE_LIMIT_WINDOW_SECONDS = 60.0


class RateLimiter:
    """Fixed-window-ish sliding limiter keyed by client IP."""

    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits.setdefault(key, deque())
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return False
        bucket.append(now)
        # Opportunistic cleanup so idle keys do not accumulate forever.
        if len(self._hits) > 4096:
            for k in [k for k, v in self._hits.items() if not v]:
                self._hits.pop(k, None)
        return True


def _cors_origins() -> list[str]:
    """Configured origins, falling back to the historical localhost pair."""
    raw = (settings.bewithme_cors_origins or "").strip()
    if not raw:
        return ["http://localhost:3000", "http://localhost:3002"]
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No request timeout — long polls (SSE on /ask/stream) and long synthesis
    # passes (/speak/stream) should be allowed to run as long as the upstream
    # is willing to. Connect/read individual chunks still time out fast.
    timeout = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)
    # trust_env=False: never route inter-service localhost traffic through a
    # system HTTP proxy (we hit a stale cached 200 from a corp proxy
    # otherwise — silent auth bypass).
    app.state.client = httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False,
    )
    app.state.auth_cache = AuthCache()
    app.state.rate_limiter = RateLimiter(_RATE_LIMIT_REQUESTS, _RATE_LIMIT_WINDOW_SECONDS)

    problems = startup_check()
    if problems:
        # Misconfigured strict mode cannot authenticate anyone; refusing to
        # boot is safer than serving in a state nobody can log into.
        for p in problems:
            log.error("auth configuration: %s", p)
        raise RuntimeError("; ".join(problems))

    if not is_strict():
        log.warning(
            "auth mode is 'legacy': X-User-Id is trusted without proof and "
            "GET /api/users is public. Safe on a private network only — see "
            "docs/SECURITY.md before exposing this shell publicly."
        )
    log.info("shell auth mode: %s", auth_mode())

    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="beWithMe shell", description="API gateway", lifespan=lifespan)

# Mount FIRST so it wraps every request (Starlette runs middlewares LIFO
# of add order — last added runs outermost). We want CORS outermost and
# event-log inside it.
install_event_log(app, service="shell")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline response hardening.

    Deliberately conservative: no CSP, because the shell also fronts the
    frontend's assets and a wrong CSP breaks the app silently. These four are
    safe for an API surface.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    return response


def _strip_hop_by_hop(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _sanitize_client_headers(headers: dict[str, str]) -> dict[str, str]:
    """Drop hop-by-hop headers AND anything a client must not control."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() not in _CLIENT_FORBIDDEN
    }


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request) -> Response:
    full_path = f"/api/{path}"
    client: httpx.AsyncClient = request.app.state.client

    public = is_public(request.method, full_path)

    # Rate-limit the credential-free surface. Authenticated traffic is already
    # bounded by needing a valid identity.
    if public:
        peer = request.client.host if request.client else "unknown"
        if not request.app.state.rate_limiter.allow(peer):
            return JSONResponse({"detail": "rate_limited"}, status_code=429)

    # Auth gate. In strict mode identity comes from a signed bearer token; in
    # legacy mode from X-User-Id. Either way the DB check still runs, so a
    # deleted user loses access immediately.
    user_id: str | None = None
    if not public:
        user_id = resolve_identity(request.headers)
        if user_id is None:
            detail = (
                "missing or invalid Authorization bearer token"
                if is_strict()
                else "missing or invalid X-User-Id"
            )
            return JSONResponse({"detail": detail}, status_code=401)
        ok = await verify_against_knowledge(
            client, user_id, request.app.state.auth_cache
        )
        if not ok:
            return JSONResponse({"detail": "unknown_user"}, status_code=401)

    service = route_for_path(path)
    target = f"{upstream_url(service)}/api/{path}"

    # The client's own X-User-Id never survives this call; we re-add only the
    # id the shell itself derived.
    headers = _sanitize_client_headers(dict(request.headers))
    if user_id is not None:
        headers["X-User-Id"] = user_id

    body = await request.body()

    upstream_req = client.build_request(
        request.method,
        target,
        headers=headers,
        params=request.query_params,
        content=body if body else None,
    )

    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.ConnectError as e:
        return Response(
            content=f"Upstream {service} ({target}) unreachable: {e}",
            status_code=502,
            media_type="text/plain",
        )

    resp_headers = _strip_hop_by_hop(dict(upstream_resp.headers))

    async def relay():
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
    )


@app.get("/")
async def root():
    return {"service": "shell", "ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.shell.main:app",
        host="0.0.0.0",
        port=service_port("shell"),
        reload=False,
    )


if __name__ == "__main__":
    main()
