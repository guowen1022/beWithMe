"""Service topology — the cross-cutting deployment shape.

Loads `config.yaml` at the project root for `base_port` and `service_host`,
then exposes the routing table + URL helpers used by both the shell (for
proxying) and each sidecar (for binding to its own port).

Override knobs (highest precedence first):
  * `<NAME>_SERVICE_URL` (per-service URL — useful for cross-host)
  * `BASE_PORT` env var (slides the whole topology — used by tests)
  * `SERVICE_HOST` env var
  * `config.yaml` at the repo root
  * pydantic defaults below
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel


_REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH: Final[Path] = _REPO_ROOT / "config.yaml"


class TopologyConfig(BaseModel):
    base_port: int = 8000
    service_host: str = "localhost"


def _load_config() -> TopologyConfig:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}
    return TopologyConfig(**raw)


config = _load_config()


# Fixed offsets — order matters; do not reorder, only append.
SERVICE_OFFSETS: Final[dict[str, int]] = {
    "shell": 0,
    "persona": 1,   # was "ask" in earlier topology
    "knowledge": 2,
    "transcribe": 3,
    "speak": 4,
    "browser": 5,
    "maestro": 6,   # PR-4: long-instance reasoning over the event stream
}

# /api/<prefix> → service. Anything not listed routes to "knowledge".
PREFIX_TO_SERVICE: Final[dict[str, str]] = {
    # Agent-driven endpoints — handled by the persona sidecar (the teacher).
    "ask":             "persona",
    "interactions":    "persona",
    "goals":           "persona",
    "sessions":        "persona",
    "concepts":        "persona",   # teacher's mastery model (ConceptNode)
    "graph":           "persona",   # teacher's concept graph data
    "dynamic":         "persona",   # dynamic UI back-channel (SSE stream + push/error)
    "perception":      "persona",   # ambient_mic block → /api/perception/utterance
    "agent":           "persona",   # PR-5: /api/agent/kickoff webhook from maestro
    "skills":          "persona",   # frontend block skill files (JS assets for note block)
    "preferences":     "persona",   # teacher's distilled preference view (TeacherPreferenceModel); on the persona sidecar so the knowledge sidecar needn't import the teacher (F7)
    # Stateless infra
    "transcribe": "transcribe",
    "eou":        "transcribe",   # text turn-detector — sibling endpoint
    "speak":      "speak",
    "browser":    "browser",
    # Maestro long-instance (PR-4). /api/maestro/event webhook fires from
    # the agent layer when a new event worth gating on arrives.
    "maestro":    "maestro",
    # Multi-persona feed surface — assembled + blended by the Maestro.
    "feed":       "maestro",
}
DEFAULT_SERVICE: Final[str] = "knowledge"


def base_port() -> int:
    """BASE_PORT env > config.yaml > pydantic default."""
    override = os.environ.get("BASE_PORT")
    return int(override) if override else config.base_port


def service_host() -> str:
    """SERVICE_HOST env > config.yaml > pydantic default."""
    return os.environ.get("SERVICE_HOST") or config.service_host


def service_port(service: str) -> int:
    return base_port() + SERVICE_OFFSETS[service]


def upstream_url(service: str) -> str:
    """Resolve the base URL for a sidecar.

    Precedence: `<NAME>_SERVICE_URL` > computed from base_port + service_host.
    """
    override = os.environ.get(f"{service.upper()}_SERVICE_URL")
    if override:
        return override.rstrip("/")
    return f"http://{service_host()}:{service_port(service)}"


def route_for_path(path: str) -> str:
    """Return the service name handling a given /api/... path.

    `path` comes in like "ask/stream" or "documents/upload" (leading /api stripped).
    """
    head = path.split("/", 1)[0]
    return PREFIX_TO_SERVICE.get(head, DEFAULT_SERVICE)
