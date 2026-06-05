"""Unit tests for posture transition rules (PR-6).

`permit_transition(old, candidate, *, user_initiated)` is the gate the
short instance applies before writing a refreshed posture. Tests pin
the monotonic + terminal rules so a future change ('let wind_down
flip back to steady on a flow_marker') has to actively delete a test.
"""
from __future__ import annotations

import pytest

from services.maestro.posture import (
    VALID_POSTURES, permit_transition,
)


# --- First write / no-op -----------------------------------------------------


def test_first_write_accepts_any_valid_posture():
    final, note = permit_transition(None, "deepen")
    assert final == "deepen"
    assert "first" in note


def test_unchanged_posture_is_noop():
    final, note = permit_transition("steady", "steady")
    assert final == "steady"
    assert "no change" in note


# --- Allowed transitions -----------------------------------------------------


@pytest.mark.parametrize("candidate", [
    "steady", "deepen", "pivot", "hold", "wind_down", "escalate", "interrupt_now",
])
def test_steady_can_move_to_anything(candidate):
    final, note = permit_transition("steady", candidate)
    assert final == candidate
    if candidate != "steady":
        assert "allowed" in note


def test_deepen_can_move_to_steady():
    final, _ = permit_transition("deepen", "steady")
    assert final == "steady"


# --- Monotonic blocks --------------------------------------------------------


@pytest.mark.parametrize("monotonic_old", ["wind_down", "pivot", "hold"])
def test_monotonic_postures_block_softer_candidates_without_user_signal(monotonic_old):
    for candidate in ("steady", "deepen"):
        final, note = permit_transition(monotonic_old, candidate)
        assert final == monotonic_old, (
            f"{monotonic_old!r} should hold against soft candidate {candidate!r}"
        )
        assert "blocked" in note


@pytest.mark.parametrize("monotonic_old", ["wind_down", "pivot", "hold"])
def test_monotonic_postures_lift_on_user_initiated(monotonic_old):
    final, _ = permit_transition(monotonic_old, "steady", user_initiated=True)
    assert final == "steady"


def test_monotonic_can_escalate_to_harder_posture_without_user_signal():
    """wind_down → interrupt_now (harder posture) is allowed even
    monotonically — escalation is always allowed, only relaxation is blocked.
    Strictly, the current implementation blocks ALL outgoing transitions
    from a monotonic posture; this test pins that behavior so a later
    relaxation (allow ratcheting harder) is an explicit deletion."""
    final, note = permit_transition("wind_down", "interrupt_now")
    # Current policy: BLOCKED. If we later want to permit hardening,
    # delete this assertion and update the test name.
    assert final == "wind_down"
    assert "blocked" in note


# --- Terminal postures -------------------------------------------------------


@pytest.mark.parametrize("terminal_old", ["escalate", "interrupt_now"])
def test_terminal_postures_block_all_outgoing(terminal_old):
    for candidate in VALID_POSTURES:
        if candidate == terminal_old:
            continue
        final, note = permit_transition(terminal_old, candidate)
        assert final == terminal_old, (
            f"terminal {terminal_old!r} should block transition to {candidate!r}"
        )


@pytest.mark.parametrize("terminal_old", ["escalate", "interrupt_now"])
def test_terminal_postures_block_even_user_initiated(terminal_old):
    """Terminal postures hold even on user re-engagement. The engagement
    has to actually end + restart to lift them (cache entry dropped)."""
    final, _ = permit_transition(terminal_old, "steady", user_initiated=True)
    assert final == terminal_old


# --- Invalid input -----------------------------------------------------------


def test_unknown_posture_rejected_keeps_old():
    final, note = permit_transition("steady", "made_up_posture")
    assert final == "steady"
    assert "rejected" in note


def test_unknown_posture_with_no_old_falls_back_to_steady():
    final, note = permit_transition(None, "made_up_posture")
    assert final == "steady"
    assert "rejected" in note
