"""Project-wide event log.

A single append-only JSONL file every sidecar and the frontend writes to.
One line per event, structured so we can grep, jq, or feed it into a
benchmark/analytics pass.

Default sink: `logs/events.jsonl` under the repo root. Override with
`EVENT_LOG_PATH=/abs/path/to/file.jsonl`. The service name is auto-stamped
from `EVENT_LOG_SERVICE` (or "unknown" if unset).

Design notes:
  * Sync, fire-and-forget. The fastapi middleware and async code call this
    from inside event loops; we never await it. Writing one short line per
    event is fast enough not to block.
  * Thread + fork safe via a process-wide threading.Lock and re-opening on
    each write (cheap append-only). Survives uvicorn --reload child PIDs.
  * Never raises. Observability must not break the underlying call.
  * Records `ts` (ISO-8601 UTC), `service`, `pid`, `kind`, plus any
    user-supplied fields. Unknown types are coerced via repr().
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PATH = _REPO_ROOT / "logs" / "events.jsonl"
_WRITE_LOCK = threading.Lock()


def _resolve_path() -> Path:
    override = os.environ.get("EVENT_LOG_PATH")
    return Path(override) if override else _DEFAULT_PATH


def _service_name() -> str:
    return os.environ.get("EVENT_LOG_SERVICE", "unknown")


def _coerce(value: Any) -> Any:
    """JSON-safe coercion. Anything we can't serialize becomes repr()."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    return repr(value)


def log_event(kind: str, **fields: Any) -> None:
    """Append one event to the project event log. Never raises."""
    try:
        path = _resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "t_mono": round(time.monotonic(), 6),
            "service": _service_name(),
            "pid": os.getpid(),
            "kind": kind,
        }
        for k, v in fields.items():
            record[k] = _coerce(v)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _WRITE_LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:  # noqa: BLE001 — observability must never bubble up
        pass


def event_log_path() -> Path:
    """Used by tests / `/api/events` ingestion to surface the active sink."""
    return _resolve_path()


__all__ = ["log_event", "event_log_path"]
