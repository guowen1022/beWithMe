"""Tier-1 eval signal for `tool.present_coordinate_grid` — the well-formedness floor.

The counterpart to `scorer.py`'s canvas-writer replay, and deliberately its
opposite in cost and character:

  * `scorer.py`    — replays a real writer turn, scores with an LLM judge.
                     Slow (~10s) and stochastic (measured judge p(deviate)
                     0.00–0.33 on identical inputs).
  * `scorer_grid`  — calls the SAME `normalize_spec` the production tool calls
                     and reports accept/reject. Microseconds, no LLM, no render,
                     no network. Identical input → identical output, always.

**Why a deterministic tier exists at all.** skillforge's gate already requires
BOTH a deterministic `ok` and a judge `quality` (`harness.gate`) — the
deterministic layer is what blocks judge-hacking. This module is that layer for
the grid tool. It is NOT a replacement for judgement: whether a grid actually
*illuminates* a concept is a taste call that needs an LLM (tier 2, and the
reason this tunable is registered `oracle_regime="validate"`). What tier 1
answers is narrower and mechanical: *is this spec even well-formed?*

That narrowness buys one property nothing else here has: **a tier-1 failure is
always a real defect, never noise.** When a canvas_guides scenario scores 0.0
it could be a genuine regression, a flaky judge, or a truncated 10s timeout
failing closed — we spent a session unable to tell those apart. A grid_spec
failure has exactly one cause, and it comes with the reason in words.

`quality` is deliberately BINARY (1.0 accept / 0.0 reject). A well-formedness
check has no meaningful gradient, and inventing one (say, "fraction of fields
that validated") would manufacture a signal the oracle cannot actually justify
— which is how a gate starts measuring its own noise. The real gradient is
tier 2's judge.
"""
from __future__ import annotations

import traceback
from typing import Any, Dict

from workshop.canvas.tools import _manim_scene

_FAIL: Dict[str, object] = {"ok": False, "quality": 0.0, "outcome": 0.0}


def _tuned_max_duration(config: dict) -> Any:
    """The bounded `max_duration` override, applied exactly as the production
    tool applies it (present_coordinate_grid.py). Out-of-range values are
    ignored rather than clamped — skillforge may TIGHTEN the code-owned budget,
    never widen it, and a candidate proposing an illegal cap must not silently
    get a legal one."""
    value = (config or {}).get("max_duration")
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and _manim_scene.DURATION_MIN_S <= value <= _manim_scene.DURATION_MAX_S):
        return float(value)
    return None


async def score(*, body: str, config: dict, scenario: dict) -> Dict[str, object]:
    """One grid-spec scenario → {ok, quality, outcome, reason}. Never raises.

    The scenario's `spec_args` are the tool arguments a teacher would emit.
    `expect` is `"accept"` or `"reject"`; a reject scenario may additionally
    pin `expect_reason` (a substring of the ValueError) so the test asserts
    *why* it was rejected, not merely that it was — a spec rejected for the
    wrong reason is a silently broken validator.

    `body` (the tuned tool description) is NOT consumed here: at tier 1 the
    input is a fixed spec, so the description cannot influence the outcome.
    It is tier 2 — where an LLM authors the spec FROM that description — that
    makes the description matter. Accepting the argument keeps the eval
    contract uniform across both tiers.
    """
    try:
        spec_args = scenario.get("spec_args")
        expect = str(scenario.get("expect") or "").strip().lower()
        if not isinstance(spec_args, dict) or expect not in ("accept", "reject"):
            # A malformed scenario is a framework/authoring bug, not a candidate
            # failure — but fail CLOSED so it can never promote anything.
            return {**_FAIL, "reason": "malformed scenario: need spec_args + expect"}

        try:
            spec = _manim_scene.normalize_spec(spec_args)
        except ValueError as exc:
            actual_reason = str(exc)
            if expect == "reject":
                want = str(scenario.get("expect_reason") or "").strip()
                if want and want.lower() not in actual_reason.lower():
                    # Rejected, but for the wrong reason: the validator moved.
                    return {**_FAIL,
                            "reason": f"rejected on {actual_reason!r}, expected {want!r}"}
                return {"ok": True, "quality": 1.0, "outcome": 1.0,
                        "reason": f"correctly rejected: {actual_reason}"}
            return {**_FAIL, "reason": f"should have been accepted, rejected: {actual_reason}"}

        if expect == "reject":
            return {**_FAIL, "reason": "should have been rejected, was accepted"}

        # Accepted as expected. Optional structural expectations let a scenario
        # assert the spec is not merely legal but the RIGHT SHAPE for the ask
        # (e.g. a shear-matrix request must actually carry a `transform`).
        missing = [f for f in (scenario.get("expect_fields") or [])
                   if spec.get(f) in (None, [], "")]
        if missing:
            return {**_FAIL, "reason": f"accepted but missing expected fields: {missing}"}

        capped = _tuned_max_duration(config)
        if capped is not None and spec["duration"] > capped:
            # The production tool tightens duration AFTER normalize_spec; mirror
            # it so a candidate proposing a cap is scored on what would ship.
            spec["duration"] = capped
        return {"ok": True, "quality": 1.0, "outcome": 1.0,
                "reason": f"accepted (duration={spec['duration']}s)"}
    except Exception:
        traceback.print_exc()
        return {**_FAIL, "reason": "scorer error"}


__all__ = ["score"]
