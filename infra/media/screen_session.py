"""Per-session state for live screen-share processing.

Holds the cross-chunk state that the per-chunk processor (router) reads
and writes:

  - `last_kept_phash` — carries dedup across chunk boundaries so a static
    screen described once doesn't get re-described N chunks in a row.
  - `in_flight_chunks` — bounded queue depth so a slow Doubao quota or
    network blip can't accumulate unbounded backlog. Caller checks
    `acquire_chunk_slot()` before processing; on False, drops the chunk
    and reports it via perception so the persona sees the gap.
  - `started_at_wall_ms` — origin for relative→wall-clock rebasing.

In-memory only (single persona-sidecar process). On restart, sessions
are forgotten — the frontend will start a new session anyway.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional
from uuid import UUID


_MAX_IN_FLIGHT_CHUNKS = 3


@dataclass
class ScreenSession:
    user_id: UUID
    session_id: str
    started_at_wall_ms: int
    source_name: Optional[str] = None
    last_kept_phash: object = None  # imagehash.ImageHash; loose-typed to avoid the import here
    in_flight_chunks: int = 0
    next_seq: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_SESSIONS: Dict[str, ScreenSession] = {}


def get_or_create(
    *,
    user_id: UUID,
    session_id: str,
    started_at_wall_ms: int,
    source_name: Optional[str] = None,
) -> ScreenSession:
    s = _SESSIONS.get(session_id)
    if s is None:
        s = ScreenSession(
            user_id=user_id,
            session_id=session_id,
            started_at_wall_ms=started_at_wall_ms,
            source_name=source_name,
        )
        _SESSIONS[session_id] = s
    elif source_name and not s.source_name:
        s.source_name = source_name
    return s


def get(session_id: str) -> Optional[ScreenSession]:
    return _SESSIONS.get(session_id)


def drop(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)


def try_acquire_chunk_slot(session: ScreenSession) -> bool:
    """Return True if there's room in the in-flight queue, False otherwise.

    Caller is responsible for calling `release_chunk_slot` in a finally
    block. The check + increment isn't atomic across coroutines; this is
    fine because chunks for one session arrive serially from one client
    and are awaited inside the route handler before the next chunk hits.
    """
    if session.in_flight_chunks >= _MAX_IN_FLIGHT_CHUNKS:
        return False
    session.in_flight_chunks += 1
    return True


def release_chunk_slot(session: ScreenSession) -> None:
    session.in_flight_chunks = max(0, session.in_flight_chunks - 1)


def next_seq(session: ScreenSession) -> int:
    n = session.next_seq
    session.next_seq += 1
    return n


def _reset_for_tests() -> None:
    _SESSIONS.clear()
