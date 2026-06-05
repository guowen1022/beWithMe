"""Unit tests for the heuristic trigger gate (PR-4).

Pure function — no DB, no LLM, no sidecar. Just rules over a synthetic
GateInput.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.maestro.gate import (
    GateInput, INBOX_STOCK_CAP, MIN_QUIET_AFTER_ENGAGEMENT, decide,
)


_USER = uuid4()
_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)


def _g(**overrides) -> GateInput:
    defaults = dict(
        user_id=_USER,
        triggering_kind="user.engagement_ended",
        now=_NOW,
        last_engagement_ended=None,
        open_inbox_count=0,
        due_followups_count=0,
    )
    defaults.update(overrides)
    return GateInput(**defaults)


# --- SILENCE paths ---------------------------------------------------------


def test_default_silence_for_unknown_kind():
    d = decide(_g(triggering_kind="some.random.kind"))
    assert d.decision == "SILENCE"
    assert "no rule fired" in d.rationale


def test_silence_when_inbox_at_cap():
    d = decide(_g(open_inbox_count=INBOX_STOCK_CAP))
    assert d.decision == "SILENCE"
    assert "inbox at cap" in d.rationale


def test_silence_when_inbox_over_cap():
    d = decide(_g(open_inbox_count=INBOX_STOCK_CAP + 3))
    assert d.decision == "SILENCE"


def test_silence_during_cool_down_after_engagement():
    d = decide(_g(
        triggering_kind="user.engagement_ended",
        last_engagement_ended=_NOW - timedelta(seconds=60),
    ))
    assert d.decision == "SILENCE"
    assert "cool-down" in d.rationale


def test_silence_for_engagement_ended_without_substrate():
    d = decide(_g(
        triggering_kind="user.engagement_ended",
        last_engagement_ended=_NOW - MIN_QUIET_AFTER_ENGAGEMENT - timedelta(seconds=1),
        due_followups_count=0,
    ))
    assert d.decision == "SILENCE"
    assert "no due followups" in d.rationale


# --- ACT paths -------------------------------------------------------------


def test_act_on_due_followups_beats_engagement_ended_rule():
    """A due followup is a stronger signal than engagement-ended-without-
    substrate, even when the triggering event is engagement_ended."""
    d = decide(_g(
        triggering_kind="user.engagement_ended",
        last_engagement_ended=_NOW - MIN_QUIET_AFTER_ENGAGEMENT - timedelta(minutes=5),
        due_followups_count=2,
    ))
    assert d.decision == "ACT"
    assert "2 followup" in d.rationale


def test_act_on_capture_event():
    d = decide(_g(triggering_kind="capture.created"))
    assert d.decision == "ACT"
    assert "capture event" in d.rationale


def test_act_on_capture_with_any_suffix():
    d = decide(_g(triggering_kind="capture.photo_uploaded"))
    assert d.decision == "ACT"


# --- Rule priority ---------------------------------------------------------


def test_inbox_cap_beats_followups():
    """When inbox is full, even a due followup gets SILENCE — don't pile on."""
    d = decide(_g(
        triggering_kind="user.engagement_ended",
        open_inbox_count=INBOX_STOCK_CAP,
        due_followups_count=5,
    ))
    assert d.decision == "SILENCE"
    assert "inbox at cap" in d.rationale


def test_cool_down_beats_followups():
    """A user who JUST closed an engagement gets SILENCE even if followups
    are due. They're cooling down; the followups will still be due in 10 min."""
    d = decide(_g(
        triggering_kind="user.engagement_ended",
        last_engagement_ended=_NOW - timedelta(minutes=1),
        due_followups_count=3,
    ))
    assert d.decision == "SILENCE"
    assert "cool-down" in d.rationale


# --- Propensity sanity -----------------------------------------------------


@pytest.mark.parametrize("input_kwargs", [
    {"open_inbox_count": INBOX_STOCK_CAP},
    {"triggering_kind": "user.engagement_ended", "last_engagement_ended": _NOW - timedelta(seconds=30)},
    {"triggering_kind": "capture.created"},
    {"due_followups_count": 1},
    {},
])
def test_propensity_in_valid_range(input_kwargs):
    d = decide(_g(**input_kwargs))
    assert 0.0 < d.propensity <= 1.0
