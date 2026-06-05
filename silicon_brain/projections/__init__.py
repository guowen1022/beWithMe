"""Projections — read-optimised views over the per-user event stream.

Per SPEC §8.4. Each projection is a small async function that takes an
`AsyncSession` and a `user_id` and returns a JSON-able dict derived from
the user's events.

Phase-0 design: **on-read materialization**. No projection tables, no
background workers — the projection function reads the stream and
recomputes the answer each call. `read_projection` returns slightly stale
data only if multiple writers race; `query_stream` is always ground
truth. This matches IMPLEMENTATION.md §6.12.

To add a projection: write `silicon_brain/projections/<name>.py` exposing
an async `materialize(session, user_id) -> dict`, then register it in
`PROJECTIONS` below.

Names listed in `PROJECTIONS` but pointing to `current_engagement_state`'s
stub-handler are Phase-0 placeholders — they return `{"_stub": True,
"name": ...}` so callers can wire against the contract before the
implementing PR lands.
"""
from __future__ import annotations

from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.projections import (
    current_aspirations,
    current_engagement_state,
    current_preferences,
    current_profile,
    due_followups,
    recent_observations_by_topic,
    recent_turns,
)


Materializer = Callable[[AsyncSession, UUID], Awaitable[dict]]


PROJECTIONS: dict[str, Materializer] = {
    "current_engagement_state": current_engagement_state.materialize,
    "current_profile": current_profile.materialize,
    "current_preferences": current_preferences.materialize,
    "due_followups": due_followups.materialize,
    "current_aspirations": current_aspirations.materialize,
    "recent_observations_by_topic": recent_observations_by_topic.materialize,
    "recent_turns": recent_turns.materialize,
}


def projection_names() -> list[str]:
    return sorted(PROJECTIONS.keys())


__all__ = ["PROJECTIONS", "Materializer", "projection_names"]
