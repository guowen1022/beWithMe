"""Unit tests for the Phase-0 replay scaffold (PR-8 begins).

Direct-construct KickoffRecord objects and exercise summarise() — no
DB. The DB-backed replay_user() is exercised end-to-end by other
tests that already produce kickoff_decision + inbox_interaction rows.
"""
from __future__ import annotations

from maestro_training.replay import KickoffRecord, _classify_outcome, summarise


def _rec(decision: str, k: int = 0, taps: int = 0, dismisses: int = 0, expires: int = 0,
         propensity: float = 0.8) -> KickoffRecord:
    r = KickoffRecord(
        kickoff_event_id="x",
        user_id="u",
        ts="2026-06-05T00:00:00+00:00",
        decision=decision,
        rationale="r",
        propensity=propensity,
        k=k,
        tap_count=taps,
        dismiss_count=dismisses,
        expire_count=expires,
    )
    r.outcome = _classify_outcome(r)
    return r


# --- Outcome classification ----------------------------------------------


def test_silence_is_no_action():
    r = _rec("SILENCE")
    assert r.outcome == "no_action"


def test_act_with_zero_k_is_no_action():
    r = _rec("ACT", k=0)
    assert r.outcome == "no_action"


def test_any_tap_wins():
    r = _rec("ACT", k=3, taps=1, dismisses=1, expires=1)
    assert r.outcome == "tap_any"


def test_all_expire():
    r = _rec("ACT", k=2, expires=2)
    assert r.outcome == "expire_all"


def test_all_dismiss():
    r = _rec("ACT", k=2, dismisses=2)
    assert r.outcome == "dismiss_all"


def test_partial_actions_is_mixed():
    r = _rec("ACT", k=3, dismisses=1, expires=1)
    assert r.outcome == "mixed"


# --- Summary metrics ------------------------------------------------------


def test_empty_records_returns_zeros():
    s = summarise([])
    assert s.total_decisions == 0
    assert s.tap_rate == 0.0
    assert s.silence_share == 0.0


def test_silence_share_counted_correctly():
    records = [
        _rec("SILENCE"),
        _rec("SILENCE"),
        _rec("ACT", k=1, taps=1),
        _rec("ACT", k=1, dismisses=1),
    ]
    s = summarise(records)
    assert s.total_decisions == 4
    assert s.silence_count == 2
    assert s.silence_share == 0.5
    assert s.act_count == 2


def test_act_rates_normalised_over_act_only():
    """tap/dismiss/expire rates are over ACT decisions, not all."""
    records = [
        _rec("SILENCE"),
        _rec("ACT", k=2, taps=1),
        _rec("ACT", k=2, expires=2),
        _rec("ACT", k=1, dismisses=1),
    ]
    s = summarise(records)
    # 3 ACT decisions; 1 tap_any, 1 expire_all, 1 dismiss_all
    assert abs(s.tap_rate - 1 / 3) < 1e-6
    assert abs(s.expire_rate - 1 / 3) < 1e-6
    assert abs(s.dismiss_rate - 1 / 3) < 1e-6


def test_propensity_buckets_coarse():
    records = [
        _rec("SILENCE", propensity=0.95),
        _rec("SILENCE", propensity=0.85),
        _rec("ACT", k=1, taps=1, propensity=0.85),
        _rec("ACT", k=1, dismisses=1, propensity=0.65),
        _rec("SILENCE", propensity=0.50),
    ]
    s = summarise(records)
    assert s.propensity_buckets["0.90+"] == 1
    assert s.propensity_buckets["0.80-0.89"] == 2
    assert s.propensity_buckets["0.60-0.69"] == 1
    assert s.propensity_buckets["<0.60"] == 1


def test_outcome_counts_match_individual_classifications():
    records = [
        _rec("SILENCE"),
        _rec("ACT", k=2, taps=1),
        _rec("ACT", k=2, taps=2),
        _rec("ACT", k=2, expires=2),
    ]
    s = summarise(records)
    assert s.outcome_counts["no_action"] == 1
    assert s.outcome_counts["tap_any"] == 2
    assert s.outcome_counts["expire_all"] == 1
