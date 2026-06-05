"""Posture transition rules (SPEC §5.7 + IMPL §6.10).

The Maestro short instance writes posture on every refresh. Most
transitions are open — `steady` can move to anything as evidence
warrants. But a small set of transitions are MONOTONIC: once the
Maestro commits to closing an engagement (`wind_down`), redirecting it
(`pivot`), or pausing it (`hold`), those decisions don't reverse
without a strong contrary signal. Without monotonicity the short
instance flip-flops on every turn and the agent gets whiplash.

This module is the policy. `permit_transition` returns the posture
that should actually be written, given (old, candidate). When the
transition is allowed it returns `candidate`; when blocked it returns
`old` (the candidate is silently rejected).
"""
from __future__ import annotations

from typing import Optional


VALID_POSTURES = frozenset({
    "steady", "deepen", "pivot", "hold",
    "wind_down", "escalate", "interrupt_now",
})


# Postures the SPEC §16.3 treats as monotonic-once-set. The short
# instance cannot move OUT of these without a strong contrary signal;
# the only way out is either:
#   - an explicit user re-engagement signal (caller passes
#     `user_initiated=True`)
#   - the engagement ends, at which point the cache entry is dropped
#     and the next engagement starts from `steady`
_MONOTONIC = frozenset({"wind_down", "pivot", "hold"})

# `escalate` and `interrupt_now` are TERMINAL — once committed, they
# do not transition at all until the engagement ends.
_TERMINAL = frozenset({"escalate", "interrupt_now"})


def permit_transition(
    old: Optional[str],
    candidate: str,
    *,
    user_initiated: bool = False,
) -> tuple[str, str]:
    """Return (final_posture, decision_note).

    `old` is None on the very first refresh (no prior entry). In that
    case the candidate is always allowed. `user_initiated` lifts the
    monotonic block — the short instance passes True when the trigger
    is an explicit re-engagement signal (user typed/spoke after a
    wind_down close, etc.).
    """
    if candidate not in VALID_POSTURES:
        # Don't blindly trust callers — refuse and stick with prior.
        return (old or "steady", f"rejected: unknown posture {candidate!r}")

    if old is None:
        return (candidate, "first write")

    if old == candidate:
        return (candidate, "no change")

    if old in _TERMINAL:
        # Terminal postures never transition until engagement ends.
        return (old, f"terminal {old!r} — candidate {candidate!r} blocked")

    if old in _MONOTONIC and not user_initiated:
        return (old, f"monotonic {old!r} held — candidate {candidate!r} blocked")

    return (candidate, "transition allowed")
