"""Views — historical log queries over the per-user event stream.

Per SPEC §15.4 / IMPLEMENTATION.md §1.9, "logs" in the v2 architecture
are views over `silicon_brain.event` filtered/joined by kind. They
parallel `silicon_brain/projections/`:

  - Projections answer "what is the user's CURRENT state?" (point-in-time
    snapshots, dict-shaped).
  - Views answer "what HAPPENED?" (chronological lists, list-shaped).

Phase-0 design matches projections: on-read Python materializers in
this package. No projection tables, no background workers, no SQL
views — yet. Phase 1+ may migrate hot views to actual SQL views or a
time-series store; the registry's contract stays stable across that
swap.

Add a view: write `silicon_brain/views/<name>.py` exposing
`async def materialize(session, user_id) -> list[dict]`, then register
in `VIEWS` below.
"""
from __future__ import annotations

from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.views import (
    cache_refresh_log,
    engagement_log,
    inbox_interaction_log,
    kickoff_log,
)


Materializer = Callable[[AsyncSession, UUID], Awaitable[list[dict]]]


VIEWS: dict[str, Materializer] = {
    "engagement_log": engagement_log.materialize,
    "kickoff_log": kickoff_log.materialize,
    "cache_refresh_log": cache_refresh_log.materialize,
    "inbox_interaction_log": inbox_interaction_log.materialize,
}


def view_names() -> list[str]:
    return sorted(VIEWS.keys())


__all__ = ["VIEWS", "Materializer", "view_names"]
