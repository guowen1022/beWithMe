"""Trigger gate — ACT vs SILENCE (SPEC §6.3).

Phase 0: heuristic. A small set of conservative rules that default to
SILENCE and ACT only when the substrate clearly justifies. Phase 1+
replaces the body of `decide()` with a learned classifier that reads
features over event-stream-derived projections.

The rules below are deliberately legible — every Phase-0 ACT decision
should be explainable in one line. Off-policy training in PR-8 will use
the heuristic's logged decisions as the behavior policy, so keeping it
simple keeps the IPS correction tractable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID


Decision = Literal["ACT", "SILENCE"]


@dataclass
class GateInput:
    """Everything the heuristic gate needs to decide. Assembled by the
    long instance from the event stream + projections before calling
    `decide()`."""

    user_id: UUID
    # Kind of event that woke us. Different kinds get different priors.
    triggering_kind: str
    # Now (tz-aware) — passed in for determinism in tests.
    now: datetime
    # Last engagement_ended ts for this user (None if none ever).
    last_engagement_ended: Optional[datetime] = None
    # Number of inbox proposals currently visible for this user. Caps at
    # SPEC §6.1.2 (M=5) — past that the gate prefers SILENCE.
    open_inbox_count: int = 0
    # Pending followups due now (count of `agent.followup_scheduled`
    # events with valid_at <= now and no observed action).
    due_followups_count: int = 0


@dataclass
class GateDecision:
    decision: Decision
    rationale: str
    # Propensity: heuristic's confidence in the decision. Logged on the
    # kickoff_decision body for PR-8's IPS correction.
    propensity: float


# Conservative knobs. Easy to surface in config later when there's a
# reason to tune per-deployment.
INBOX_STOCK_CAP = 5            # SPEC §6.1.2
MIN_QUIET_AFTER_ENGAGEMENT = timedelta(minutes=10)


def decide(g: GateInput) -> GateDecision:
    """Heuristic ACT/SILENCE.

    Rules, in priority order:

      1. SILENCE if inbox is full (>= INBOX_STOCK_CAP). Don't pile on.
      2. SILENCE if the user just finished an engagement within
         MIN_QUIET_AFTER_ENGAGEMENT — they're cooling down.
      3. ACT on `signal.followup_due` (a `due_followups_count > 0`
         derived kind synthesised by the long instance) — followups
         passed their `valid_at` are the most legible ACT trigger.
      4. ACT on `capture.created` — a deliberate capture is a strong
         intent signal.
      5. ACT on `user.engagement_ended` IFF there's substrate worth
         re-engaging on. Phase 0 proxies "worth it" as
         `due_followups_count > 0` — otherwise SILENCE.
      6. Default SILENCE.

    Propensity values are eyeballed; PR-8 calibrates these against
    logged outcomes.
    """
    # Rule 1 — inbox full.
    if g.open_inbox_count >= INBOX_STOCK_CAP:
        return GateDecision(
            decision="SILENCE",
            rationale=f"inbox at cap ({g.open_inbox_count}/{INBOX_STOCK_CAP}); don't pile on",
            propensity=0.95,
        )

    # Rule 2 — cool-down right after an engagement.
    if (
        g.last_engagement_ended is not None
        and (g.now - g.last_engagement_ended) < MIN_QUIET_AFTER_ENGAGEMENT
    ):
        return GateDecision(
            decision="SILENCE",
            rationale=(
                f"engagement ended {(g.now - g.last_engagement_ended).total_seconds():.0f}s "
                f"ago; under {MIN_QUIET_AFTER_ENGAGEMENT.total_seconds():.0f}s cool-down"
            ),
            propensity=0.85,
        )

    # Rule 3 — due followups (most legible ACT path).
    if g.due_followups_count > 0:
        return GateDecision(
            decision="ACT",
            rationale=f"{g.due_followups_count} followup(s) past valid_at",
            propensity=0.80,
        )

    # Rule 4 — deliberate capture.
    if g.triggering_kind.startswith("capture."):
        return GateDecision(
            decision="ACT",
            rationale=f"capture event ({g.triggering_kind}) — deliberate intent signal",
            propensity=0.70,
        )

    # Rule 5 — engagement_ended + substrate (covered by rule 3 above; if
    # we got here there are no due followups, so SILENCE wins).
    if g.triggering_kind == "user.engagement_ended":
        return GateDecision(
            decision="SILENCE",
            rationale="engagement ended but no due followups or fresh captures",
            propensity=0.75,
        )

    # Rule 6 — default.
    return GateDecision(
        decision="SILENCE",
        rationale=f"no rule fired for kind={g.triggering_kind!r}",
        propensity=0.90,
    )
