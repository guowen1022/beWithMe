"""beWithMe API gateway — :BASE_PORT.

A thin reverse proxy. Holds no DB session, no models, no Playwright. Forwards
every `/api/<prefix>/...` request to the corresponding sidecar based on
`infra.topology.PREFIX_TO_SERVICE` and streams the response back.

Run standalone (port computed from BASE_PORT env, default 8000):
    python -m services.shell
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services.shell.auth import (
    AuthCache,
    is_public,
    parse_user_header,
    verify_against_knowledge,
)
from infra.topology import route_for_path, service_port, upstream_url


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
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="beWithMe shell", description="API gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _strip_hop_by_hop(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request) -> Response:
    full_path = f"/api/{path}"
    client: httpx.AsyncClient = request.app.state.client

    # Auth gate: every protected path must carry a valid X-User-Id that maps
    # to a real user. Verified once per user with a TTL cache so the per-
    # request overhead is a dict lookup, not an HTTP roundtrip.
    if not is_public(request.method, full_path):
        user_id = parse_user_header(request.headers.get("x-user-id"))
        if user_id is None:
            return JSONResponse(
                {"detail": "missing or invalid X-User-Id"},
                status_code=401,
            )
        ok = await verify_against_knowledge(
            client, user_id, request.app.state.auth_cache
        )
        if not ok:
            return JSONResponse({"detail": "unknown_user"}, status_code=401)

    service = route_for_path(path)
    target = f"{upstream_url(service)}/api/{path}"

    headers = _strip_hop_by_hop(dict(request.headers))
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
