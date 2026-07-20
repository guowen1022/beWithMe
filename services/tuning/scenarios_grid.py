"""Tier-1 eval scenarios for `tool.present_coordinate_grid` — the canonical set.

Each scenario is a fixed spec (the tool arguments a teacher would emit) plus
what `normalize_spec` should do with it. No LLM authors anything here, which is
the point: tier 1 asks only *is this spec well-formed?*, and a failure therefore
has exactly one cause. See `scorer_grid.py` for why that property is worth a
tier of its own.

Fields:
  * ``spec_args``     — the tool args, verbatim
  * ``expect``        — "accept" | "reject"
  * ``expect_reason`` — (reject only) substring of the expected ValueError, so
                        a scenario asserts WHY it was rejected. A spec rejected
                        for the wrong reason means the validator moved, which a
                        bare pass/fail would hide.
  * ``expect_fields`` — (accept only) fields the normalized spec must carry, so
                        an "animate a shear" case can't pass with a bare curve
  * ``region``        — partition key: which capability the spec exercises
                        (function | matrix | moving_point | limits)
  * ``split``         — "train" (proposer may see it) | "holdout" (gate only)
  * ``guard``         — a failing guard vetoes promotion outright

Most cases are lifted from `tests/unit/test_manim_scene.py`'s existing
`pytest.raises` set — those are the caps that already earned a regression test,
so they are the ones worth defending in the loop too.

**Invariant: every region appears on BOTH sides of the split.** A region only in
`train` is a region whose regression the gate can never detect. Enforced by
`tests/unit/test_tuning_scenarios_grid.py`.

The set is deliberately reject-heavy. The teacher already emits valid specs most
of the time, so accepts are the easy majority and the rejects carry the signal:
each one is a contract the tool description must convey well enough that the
model does not walk into it.
"""
from __future__ import annotations

from typing import Dict, List

# The tunable this scenario set scores. Mirrors the id the tool itself resolves
# (`workshop/canvas/tools/present_coordinate_grid.py::_TUNABLE_ID`) — the two
# must agree or the served config and the eval signal describe different things.
GRID_TUNABLE_ID = "tool.present_coordinate_grid"


SCENARIOS: List[Dict[str, object]] = [
    # ---------------------------------------------------------- accepts
    {
        "input": "plot y = x^2 as an animated curve",
        "spec_args": {"title": "A parabola", "functions": [{"expression": "x*x"}]},
        "expect": "accept",
        "expect_fields": ["functions"],
        "region": "function",
        "split": "holdout",
        "guard": True,   # the simplest possible valid spec: if THIS breaks, the tool is down
    },
    {
        "input": "animate the shear matrix [[1,1],[0,1]] warping the grid",
        "spec_args": {
            "title": "Shear",
            "functions": [{"expression": "x"}],
            "transform": [[1, 1], [0, 1]],
        },
        "expect": "accept",
        # the whole reason to pick video over a static plot — a spec that drops
        # the matrix is "valid" but answers a different question
        "expect_fields": ["transform"],
        "region": "matrix",
        "split": "train",
    },
    {
        "input": "show a dot traveling along the sine wave",
        "spec_args": {
            "functions": [{"expression": "sin(x)"}],
            "moving_point": {"on": 0},
        },
        "expect": "accept",
        "expect_fields": ["moving_point"],
        "region": "moving_point",
        "split": "holdout",
    },
    {
        "input": "draw three curves together over a wide range",
        "spec_args": {
            "x_range": [-10, 10],
            "functions": [
                {"expression": "sin(x)", "color": "blue"},
                {"expression": "cos(x)", "color": "green"},
                {"expression": "sin(x)*cos(x)", "color": "red"},
            ],
        },
        "expect": "accept",
        "region": "limits",
        "split": "train",   # exactly at MAX_FUNCTIONS — the legal edge
    },

    # ---------------------------------------------------------- rejects
    {
        "input": "plot x squared, written with a caret",
        "spec_args": {"functions": [{"expression": "x^2"}]},
        "expect": "reject",
        # '^' is XOR in Python; the validator must say so in words the model
        # can act on, since this is the single most likely notation mistake
        "expect_reason": "x**2",
        "region": "function",
        "split": "train",
    },
    {
        "input": "plot something using a variable that isn't x",
        "spec_args": {"functions": [{"expression": "2*t + 1"}]},
        "expect": "reject",
        "expect_reason": "the only variable is x",
        "region": "function",
        "split": "holdout",
    },
    {
        "input": "draw four curves at once",
        "spec_args": {"functions": [{"expression": "x"}] * 4},
        "expect": "reject",
        "expect_reason": "at most",
        "region": "limits",
        "split": "holdout",
    },
    {
        "input": "plot a curve in mauve",
        "spec_args": {"functions": [{"expression": "x", "color": "mauve"}]},
        "expect": "reject",
        "expect_reason": "color",
        "region": "limits",
        "split": "train",
    },
    {
        "input": "animate a 3x3 transform",
        "spec_args": {
            "functions": [{"expression": "x"}],
            "transform": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        },
        "expect": "reject",
        "expect_reason": "2x2",
        "region": "matrix",
        "split": "holdout",
    },
    {
        "input": "animate a transform with a huge entry",
        "spec_args": {
            "functions": [{"expression": "x"}],
            "transform": [[500, 0], [0, 1]],
        },
        "expect": "reject",
        "expect_reason": "transform entries",
        "region": "matrix",
        "split": "train",
    },
    {
        "input": "move a dot along the third curve when only one exists",
        "spec_args": {
            "functions": [{"expression": "x"}],
            "moving_point": {"on": 2},
        },
        "expect": "reject",
        "expect_reason": "moving_point.on",
        "region": "moving_point",
        "split": "train",
    },
    {
        "input": "move a dot along a curve, index given as a boolean",
        "spec_args": {
            "functions": [{"expression": "x"}, {"expression": "x + 1"}],
            "moving_point": {"on": True},
        },
        "expect": "reject",
        # bool is an int subclass: True would sail through as index 1 and
        # codegen `curveTrue` -> NameError AFTER a wasted render subprocess
        "expect_reason": "moving_point.on",
        "region": "moving_point",
        "split": "holdout",
    },
]


__all__ = ["SCENARIOS", "GRID_TUNABLE_ID"]
