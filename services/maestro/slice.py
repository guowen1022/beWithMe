"""Per-candidate substrate slice retrieval (SPEC §6.4).

The Maestro long instance, on ACT, retrieves a structured slice of the
learner's state to feed into candidate generation. PR-4 covers the
shared (across-candidates) slice — per-candidate refinement comes once
candidates exist and is largely a re-scope of the same primitives
filtered by the candidate's concept/thread.

Phase 0 reads ONLY from silicon_brain (the event stream + Phase-0
projections). The persona-side concept-mastery store is not reachable
across the sidecar boundary today; PR-5+ can plumb it through a thin
HTTP endpoint when the slice starts needing it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from infra.contracts.event import EventDTO, StreamQuery
from infra.silicon_brain_client import SiliconBrainClient


@dataclass
class Slice:
    """Structured substrate passed to candidate generation."""

    user_id: UUID
    now: datetime
    triggering_event: EventDTO
    recent_observations: list[EventDTO] = field(default_factory=list)
    due_followups: list[EventDTO] = field(default_factory=list)
    recent_engagements: list[dict] = field(default_factory=list)
    profile_snapshot: dict = field(default_factory=dict)
    aspirations_snapshot: dict = field(default_factory=dict)


async def retrieve(
    client: SiliconBrainClient,
    user_id: UUID,
    triggering_event: EventDTO,
    *,
    now: Optional[datetime] = None,
    observations_lookback_days: int = 30,
    observations_limit: int = 20,
    recent_engagements_limit: int = 3,
) -> Slice:
    """Read everything Phase-0 candidate generation needs.

    Best-effort: each underlying call is wrapped so a failure in one
    source doesn't blank the whole slice. The candidate prompt will
    just see less substrate and (per the gate) prefer fewer/no candidates.
    """
    now = now or datetime.now(timezone.utc)

    observations: list[EventDTO] = []
    try:
        observations = await client.query_stream(
            user_id,
            StreamQuery(
                kinds=["agent.observation"],
                limit=observations_limit,
                order="desc",
            ),
        )
    except Exception:
        pass

    due_followups: list[EventDTO] = []
    try:
        # Use `until` to constrain to followups whose `valid_at` is now-or-past.
        # Phase 0: project lazily — query the stream for followups and filter
        # by `valid_at` client-side. The real `due_followups` projection
        # remains a stub until PR-5/PR-6.
        candidates = await client.query_stream(
            user_id,
            StreamQuery(
                kinds=["agent.followup_scheduled"],
                limit=50,
                order="desc",
            ),
        )
        due_followups = [
            f for f in candidates
            if f.valid_at is not None and f.valid_at <= now
        ]
    except Exception:
        pass

    recent_engagements: list[dict] = []
    try:
        rows = await client.read_view(user_id, "engagement_log")
        recent_engagements = list(rows)[-recent_engagements_limit:]
    except Exception:
        pass

    profile_snapshot: dict = {}
    aspirations_snapshot: dict = {}
    try:
        profile_snapshot = await client.read_projection(user_id, "current_profile")
    except Exception:
        pass
    try:
        aspirations_snapshot = await client.read_projection(user_id, "current_aspirations")
    except Exception:
        pass

    return Slice(
        user_id=user_id,
        now=now,
        triggering_event=triggering_event,
        recent_observations=observations,
        due_followups=due_followups,
        recent_engagements=recent_engagements,
        profile_snapshot=profile_snapshot,
        aspirations_snapshot=aspirations_snapshot,
    )


def render_for_prompt(s: Slice) -> str:
    """Compact human-readable rendering of a slice for the LLM prompt.

    Deliberately terse. The LLM sees the structure; verbose stream
    bodies would just burn tokens.
    """
    lines: list[str] = []
    lines.append(f"NOW: {s.now.isoformat()}")
    lines.append(
        f"TRIGGERING EVENT: kind={s.triggering_event.kind} "
        f"body={s.triggering_event.body}"
    )

    if s.due_followups:
        lines.append("")
        lines.append("DUE FOLLOWUPS:")
        for f in s.due_followups[:5]:
            lines.append(f"  - valid_at={f.valid_at.isoformat() if f.valid_at else '?'} body={f.body}")
    else:
        lines.append("")
        lines.append("DUE FOLLOWUPS: none")

    if s.recent_observations:
        lines.append("")
        lines.append("RECENT AGENT OBSERVATIONS:")
        for o in s.recent_observations[:5]:
            lines.append(f"  - ts={o.ts.isoformat()} body={o.body}")
    else:
        lines.append("")
        lines.append("RECENT AGENT OBSERVATIONS: none")

    if s.recent_engagements:
        lines.append("")
        lines.append("RECENT ENGAGEMENTS:")
        for e in s.recent_engagements:
            lines.append(f"  - {e}")

    if s.profile_snapshot and not s.profile_snapshot.get("_stub"):
        lines.append("")
        lines.append(f"PROFILE: {s.profile_snapshot}")
    if s.aspirations_snapshot and not s.aspirations_snapshot.get("_stub"):
        lines.append("")
        lines.append(f"ASPIRATIONS: {s.aspirations_snapshot}")

    return "\n".join(lines)
