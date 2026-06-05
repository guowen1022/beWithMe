"""Offline log replay — joins kickoff decisions to their downstream outcomes.

For each `maestro_long.kickoff_decision` event:
  - Find every `inbox_interaction_log` row that shares `kickoff_event_id`
  - Derive the outcome:
      tap_any        — at least one of K candidates was tapped
      dismiss_all    — all K dismissed and none tapped
      expire_all     — all K expired without action
      mixed          — anything else
      no_action      — K=0 (the SILENCE path or empty-candidates ACT)
  - Join to engagement_log if the tap led to an engagement_started
    within the re-engagement window.

The output is the per-kickoff record set PR-8's training loop will
ingest. Phase-0 surfaces:
  - tap_rate            — fraction of ACT decisions that got any tap
  - expire_rate         — fraction that all expired
  - dismiss_rate        — fraction that all dismissed
  - silence_share       — fraction of ALL kickoff_decisions that were SILENCE
  - propensity_summary  — distribution of gate propensities (sanity check
                          on the heuristic's confidence)

Phase 1 will add: listwise loss over K candidates, IPS-corrected
counterfactual estimates, engagement-quality scoring (Q-from-stream
per SPEC §14.2), and the promotion-gate deltas.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from silicon_brain.models.event import Event


_KICKOFF_KIND = "maestro_long.kickoff_decision"
_TAP_KIND = "user.proposal_tapped"
_DISMISS_KIND = "user.proposal_dismissed"
_EXPIRE_KIND = "system.proposal_expired"


@dataclass
class KickoffRecord:
    """One ACT/SILENCE decision joined to its downstream outcome."""

    kickoff_event_id: str
    user_id: str
    ts: str
    decision: str             # ACT | SILENCE
    rationale: str
    propensity: float
    k: int
    candidates: list[dict] = field(default_factory=list)
    # Outcome rollup derived from inbox_interaction_log.
    outcome: str = "no_action"   # tap_any | dismiss_all | expire_all | mixed | no_action
    tap_count: int = 0
    dismiss_count: int = 0
    expire_count: int = 0


@dataclass
class ReplaySummary:
    """Aggregated Phase-0 baseline metrics."""

    total_decisions: int = 0
    silence_count: int = 0
    act_count: int = 0
    tap_rate: float = 0.0
    dismiss_rate: float = 0.0
    expire_rate: float = 0.0
    silence_share: float = 0.0
    outcome_counts: dict[str, int] = field(default_factory=dict)
    propensity_buckets: dict[str, int] = field(default_factory=dict)


def _propensity_bucket(p: float) -> str:
    # Coarse buckets so the summary stays readable.
    if p >= 0.9: return "0.90+"
    if p >= 0.8: return "0.80-0.89"
    if p >= 0.7: return "0.70-0.79"
    if p >= 0.6: return "0.60-0.69"
    return "<0.60"


async def _all_for_user(
    session: AsyncSession, user_id: UUID, kinds: tuple[str, ...],
) -> list[Event]:
    stmt = (
        select(Event)
        .where(Event.user_id == user_id, Event.kind.in_(kinds))
        .order_by(asc(Event.ts))
    )
    return list((await session.execute(stmt)).scalars().all())


async def replay_user(session: AsyncSession, user_id: UUID) -> list[KickoffRecord]:
    """Build one KickoffRecord per maestro_long.kickoff_decision."""
    events = await _all_for_user(
        session, user_id,
        (_KICKOFF_KIND, _TAP_KIND, _DISMISS_KIND, _EXPIRE_KIND),
    )

    # Bucket interactions by kickoff_event_id for O(1) lookup.
    interactions: dict[str, list[Event]] = {}
    for ev in events:
        if ev.kind == _KICKOFF_KIND:
            continue
        body = ev.body or {}
        kid = body.get("kickoff_event_id")
        if not kid:
            continue
        interactions.setdefault(kid, []).append(ev)

    out: list[KickoffRecord] = []
    for ev in events:
        if ev.kind != _KICKOFF_KIND:
            continue
        body = ev.body or {}
        rec = KickoffRecord(
            kickoff_event_id=str(ev.event_id),
            user_id=str(ev.user_id),
            ts=ev.ts.isoformat(),
            decision=str(body.get("decision", "")),
            rationale=str(body.get("rationale", "")),
            propensity=float(body.get("propensity", 0.0)),
            k=int(body.get("k", 0)),
            candidates=list(body.get("candidates", [])),
        )
        related = interactions.get(rec.kickoff_event_id, [])
        for r in related:
            if r.kind == _TAP_KIND:
                rec.tap_count += 1
            elif r.kind == _DISMISS_KIND:
                rec.dismiss_count += 1
            elif r.kind == _EXPIRE_KIND:
                rec.expire_count += 1
        rec.outcome = _classify_outcome(rec)
        out.append(rec)
    return out


def _classify_outcome(rec: KickoffRecord) -> str:
    if rec.decision == "SILENCE" or rec.k == 0:
        return "no_action"
    if rec.tap_count > 0:
        return "tap_any"
    if rec.expire_count >= rec.k and rec.expire_count > 0:
        return "expire_all"
    if rec.dismiss_count >= rec.k and rec.dismiss_count > 0:
        return "dismiss_all"
    return "mixed"


def summarise(records: list[KickoffRecord]) -> ReplaySummary:
    """Aggregate one user's (or many users') records into the Phase-0
    baseline metrics PR-8 will measure improvements against."""
    s = ReplaySummary()
    s.total_decisions = len(records)
    if not records:
        return s

    outcomes = Counter(r.outcome for r in records)
    s.outcome_counts = dict(outcomes)
    s.silence_count = sum(1 for r in records if r.decision == "SILENCE")
    s.act_count = s.total_decisions - s.silence_count

    act_records = [r for r in records if r.decision == "ACT" and r.k > 0]
    n_act = len(act_records) or 1   # avoid divide-by-zero
    s.tap_rate = sum(1 for r in act_records if r.outcome == "tap_any") / n_act
    s.dismiss_rate = sum(1 for r in act_records if r.outcome == "dismiss_all") / n_act
    s.expire_rate = sum(1 for r in act_records if r.outcome == "expire_all") / n_act
    s.silence_share = s.silence_count / s.total_decisions

    buckets: Counter = Counter()
    for r in records:
        buckets[_propensity_bucket(r.propensity)] += 1
    s.propensity_buckets = dict(buckets)
    return s
