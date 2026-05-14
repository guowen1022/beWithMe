"""FastAPI middleware that logs every HTTP request to the project event log.

Drop-in usage from each sidecar's main.py:

    from infra.event_log_middleware import install_event_log
    install_event_log(app, service="persona")

Emits two events per request:
  * `http.start` { req_id, method, path, user_id, query }
  * `http.end`   { req_id, method, path, status, duration_ms, user_id }

`req_id` is an 8-char hex tag that lets you correlate the two lines for the
same request. Custom event emissions inside handlers should re-use the
`req_id` from `request.state.event_req_id` so a whole flow stitches together.

OPTIONS preflights are not logged (noise). Streaming endpoints emit
`http.start` immediately and `http.end` when the client disconnects.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from infra.event_log import log_event


def install_event_log(
    app,
    service: str,
    skip_paths: Iterable[str] = (),
) -> None:
    """Mount the request-logging middleware and set EVENT_LOG_SERVICE.

    `skip_paths` is a list of prefix matches (e.g. "/api/dynamic/stream")
    that should NOT emit start/end events — useful for noisy SSE channels.
    """
    # Set the per-process service tag so any in-process log_event() call
    # without an explicit service field still gets stamped.
    os.environ["EVENT_LOG_SERVICE"] = service
    skip = tuple(skip_paths)

    class _EventLogMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            method = request.method
            path = request.url.path
            if method == "OPTIONS" or any(path.startswith(p) for p in skip):
                return await call_next(request)

            req_id = uuid.uuid4().hex[:8]
            request.state.event_req_id = req_id
            user_id = request.headers.get("x-user-id") or None
            t0 = time.perf_counter()
            log_event(
                "http.start",
                req_id=req_id,
                method=method,
                path=path,
                user_id=user_id,
                query=str(request.url.query) or None,
            )
            status = 0
            try:
                response: Response = await call_next(request)
                status = response.status_code
                return response
            except Exception as exc:
                log_event(
                    "http.error",
                    req_id=req_id,
                    method=method,
                    path=path,
                    user_id=user_id,
                    error=repr(exc),
                    duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                )
                raise
            finally:
                log_event(
                    "http.end",
                    req_id=req_id,
                    method=method,
                    path=path,
                    user_id=user_id,
                    status=status,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                )

    app.add_middleware(_EventLogMiddleware)


__all__ = ["install_event_log"]
