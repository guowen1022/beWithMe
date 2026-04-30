"""Service topology + URL resolution.

Sidecars bind to `BASE_PORT + offset`, where `BASE_PORT` defaults to 8000.
Override the topology globally with the `BASE_PORT` env var (or
`base_port` in .env), or per-service with `<NAME>_SERVICE_URL` (e.g.
`KNOWLEDGE_SERVICE_URL=http://other-host:9002`).

This module is imported by both the shell (to compute upstreams) and each
sidecar's main (to compute its bind port). Keep it dependency-light.
"""
from __future__ import annotations
import os
from typing import Final

from app.config import settings


# Fixed offsets — order matters; do not reorder, only append.
SERVICE_OFFSETS: Final[dict[str, int]] = {
    "shell": 0,
    "ask": 1,
    "knowledge": 2,
    "transcribe": 3,
    "speak": 4,
    "browser": 5,
}

# /api/<prefix> → service. Anything not listed routes to "knowledge".
PREFIX_TO_SERVICE: Final[dict[str, str]] = {
    "ask": "ask",
    "interactions": "ask",
    "transcribe": "transcribe",
    "speak": "speak",
    "browser": "browser",
}
DEFAULT_SERVICE: Final[str] = "knowledge"


def service_port(service: str) -> int:
    return settings.base_port + SERVICE_OFFSETS[service]


def upstream_url(service: str) -> str:
    """Resolve the base URL for a sidecar.

    Precedence: `<NAME>_SERVICE_URL` env var > computed from base_port.
    """
    override = os.environ.get(f"{service.upper()}_SERVICE_URL")
    if override:
        return override.rstrip("/")
    return f"http://{settings.service_host}:{service_port(service)}"


def route_for_path(path: str) -> str:
    """Return the service name handling a given /api/... path."""
    # path comes in like "ask/stream" or "documents/upload" (leading /api stripped)
    head = path.split("/", 1)[0]
    return PREFIX_TO_SERVICE.get(head, DEFAULT_SERVICE)
