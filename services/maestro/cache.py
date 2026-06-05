"""In-process cache substrate (SPEC §5).

Keyed by `(user_id, persona_purpose)`. Each entry carries the paragraph
prose the agent reads on every LLM call AND a structured `posture`
field (SPEC §5.7) — `steady`, `deepen`, `pivot`, `hold`, `wind_down`,
`escalate`, `interrupt_now`.

PR-4 ships this as a skeleton. PR-5 makes the agent read it on every
LLM call. PR-6 plumbs the Maestro short instance's writes through here.
Phase 1+ swaps the in-process dict for Redis without changing the
interface — the cache is content-equivalent in privacy class to its
silicon_brain source, so per-user keys apply when persisted.

State is intentionally lost on sidecar restart (IMPLEMENTATION.md §6.3).
The Maestro long instance will rebuild on the next event; rebuilding
is cheap relative to the cost of keeping cross-restart state honest
during Phase 0.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID


VALID_POSTURES = frozenset({
    "steady", "deepen", "pivot", "hold",
    "wind_down", "escalate", "interrupt_now",
})


@dataclass
class CacheEntry:
    """One paragraph + posture for a given (user, persona-purpose)."""

    user_id: UUID
    persona_purpose: str
    paragraph: str
    posture: str = "steady"
    written_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    # The candidate index (when this entry was seeded from a top-K kickoff
    # pick). None means the entry was written by a non-kickoff path.
    candidate_idx: Optional[int] = None


class Cache:
    """Asyncio-safe in-process dict. One process per Maestro sidecar."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, str], CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, user_id: UUID, persona_purpose: str) -> Optional[CacheEntry]:
        async with self._lock:
            return self._store.get((user_id, persona_purpose))

    async def set(self, entry: CacheEntry) -> None:
        if entry.posture not in VALID_POSTURES:
            raise ValueError(
                f"unknown posture {entry.posture!r}; expected one of {sorted(VALID_POSTURES)}"
            )
        async with self._lock:
            self._store[(entry.user_id, entry.persona_purpose)] = entry

    async def update_posture(
        self, user_id: UUID, persona_purpose: str, posture: str,
    ) -> Optional[CacheEntry]:
        """Mutate posture in place; rewrite `written_at`. Returns the
        updated entry, or None if the key isn't cached yet."""
        if posture not in VALID_POSTURES:
            raise ValueError(f"unknown posture {posture!r}")
        async with self._lock:
            existing = self._store.get((user_id, persona_purpose))
            if existing is None:
                return None
            updated = CacheEntry(
                user_id=existing.user_id,
                persona_purpose=existing.persona_purpose,
                paragraph=existing.paragraph,
                posture=posture,
                written_at=datetime.now(timezone.utc),
                candidate_idx=existing.candidate_idx,
            )
            self._store[(user_id, persona_purpose)] = updated
            return updated

    async def drop(self, user_id: UUID, persona_purpose: str) -> None:
        async with self._lock:
            self._store.pop((user_id, persona_purpose), None)

    async def size(self) -> int:
        async with self._lock:
            return len(self._store)
