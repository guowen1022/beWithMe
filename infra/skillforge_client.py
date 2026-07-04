"""beWithMe's thin adapter to skillforge (the external tuning framework).

DEFAULT OFF. If `skillforge_edge_url` is empty (the default), every `resolve()`
returns a baseline (enabled, today's behavior) and `collect()` is a no-op — so
beWithMe is byte-for-byte unchanged. Set the url to enable tuning.

Design invariants (from brainstorm/tool-refining/, docs 05/13):
  * `resolve()` is a PURE LOCAL lookup on a cached snapshot — never blocks a turn.
    The cache is refreshed in a BACKGROUND thread, TTL-gated; any error fails open
    (keep the old cache).
  * `collect()` is fire-and-forget telemetry — never raises, never blocks.
  * No import of the skillforge package: we speak its HTTP protocol only (~2 shapes),
    so beWithMe has zero code dependency on the framework.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from infra.config import settings

_TTL_S = 60.0
_TIMEOUT_S = 2.0

# Module state (patchable in tests). `_edge_url` seeds from config but is a plain
# global so tests can flip it without env gymnastics.
_edge_url: str = settings.skillforge_edge_url or ""
_host: str = settings.skillforge_host or "beWithMe"

_lock = threading.Lock()
_snapshot: Dict[str, dict] = {}   # tunable_id -> entry dict
_last_refresh: float = 0.0
_refreshing: bool = False


@dataclass
class Resolved:
    tunable_id: str
    enabled: bool
    version: str
    config: dict

    def __bool__(self) -> bool:
        return self.enabled


def _baseline(tunable_id: str) -> Resolved:
    return Resolved(tunable_id=tunable_id, enabled=True, version="baseline", config={})


def enabled() -> bool:
    return bool(_edge_url)


def _do_refresh() -> None:
    global _snapshot, _last_refresh, _refreshing
    try:
        url = f"{_edge_url.rstrip('/')}/api/snapshot/{_host}"
        r = httpx.get(url, timeout=_TIMEOUT_S)
        r.raise_for_status()
        entries = (r.json() or {}).get("entries", {}) or {}
        with _lock:
            _snapshot = entries
    except Exception:
        pass  # fail open: keep the previous cache
    finally:
        with _lock:
            _last_refresh = time.monotonic()
            _refreshing = False


def _maybe_refresh() -> None:
    """Kick a background refresh if the cache is stale. Never blocks the caller."""
    global _refreshing
    if not enabled():
        return
    with _lock:
        stale = (time.monotonic() - _last_refresh) > _TTL_S
        start = stale and not _refreshing
        if start:
            _refreshing = True
    if start:
        threading.Thread(target=_do_refresh, daemon=True).start()


def resolve(tunable_id: str) -> Resolved:
    """Pure local lookup. Disabled / unknown tunable → baseline (enabled)."""
    if not enabled():
        return _baseline(tunable_id)
    _maybe_refresh()  # fire-and-forget; returns immediately
    with _lock:
        entry = _snapshot.get(tunable_id)
    if entry is None:
        return _baseline(tunable_id)
    return Resolved(
        tunable_id=tunable_id,
        enabled=bool(entry.get("enabled", True)),
        version=str(entry.get("version", "baseline")),
        config=dict(entry.get("config", {}) or {}),
    )


def collect(event: Dict[str, Any]) -> None:
    """Fire-and-forget telemetry. No-op when disabled; never raises/blocks."""
    if not enabled():
        return

    def _post() -> None:
        try:
            httpx.post(f"{_edge_url.rstrip('/')}/api/telemetry", json=event, timeout=_TIMEOUT_S)
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


def collect_result(
    tunable_id: str,
    *,
    ok: bool,
    latency_ms: Optional[int] = None,
    outcome_scalar: Optional[float] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Compose a TelemetryEvent-shaped record for one tunable execution and
    fire it. This is the call sites' surface — they report an outcome, not
    the wire shape. Derived signals only (never raw user content). No-op
    when disabled; never raises/blocks."""
    if not enabled():
        return
    collect({
        "correlation_id": correlation_id or uuid.uuid4().hex,
        "host": _host,
        "tunable_id": tunable_id,
        "variant_version": resolve(tunable_id).version,
        "result": {"ok": ok, "latency_ms": latency_ms},
        "outcome_scalar": outcome_scalar,
    })


# ---- test helpers (used by tests to inject a snapshot without a live edge) ----

def _set_for_test(edge_url: Optional[str], entries: Optional[Dict[str, dict]] = None) -> None:
    global _edge_url, _snapshot, _last_refresh, _refreshing
    with _lock:
        _edge_url = edge_url or ""
        _snapshot = dict(entries or {})
        _last_refresh = time.monotonic()  # mark fresh so no background fetch fires
        _refreshing = False


def _reset_for_test() -> None:
    _set_for_test("", {})
