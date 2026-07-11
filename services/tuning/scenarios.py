"""Held-out ground truth for `skill_menu.canvas_guides` — owned by beWithMe.

Each scenario is one canvas-writer turn with exactly one right guide. The
scorer replays the REAL writer over the candidate menu, so a scenario carries
what the production writer would actually have in hand:

  * ``input``        — the user's request (the writer's `question`)
  * ``transcript``   — the spoken answer the writer runs after (voice-leads);
                       without it the replayed writer is biased toward "the
                       voice answer was self-contained, author nothing"
  * ``expect_guide`` — the single correct menu pick ("plot" | "mermaid")
  * ``rubric``       — short judge hints: what a well-steering menu looks
                       like for this request
  * ``guard``        — a failing guard scenario vetoes promotion outright

The scorer tolerates old-shape rows already in the skillforge store (the
onboarding demo seeded `input`/`expect_guide`/`must_include` only): missing
`transcript` defaults to empty, `must_include` doubles as the rubric.

Grow this set from real failures — new scenarios can be POSTed to skillforge's
eval service at any time (registration dedups by ``input``), and every later
evaluate/gate/drift run picks them up automatically.
"""
from __future__ import annotations

from typing import Dict, List


SCENARIOS: List[Dict[str, object]] = [
    {
        "input": "plot the parabola y = x^2 over [-3, 3]",
        "transcript": (
            "A parabola is the U-shaped curve you get from squaring x — it "
            "bottoms out at the origin and rises symmetrically on both sides. "
            "Let me put the actual curve on canvas so you can see the shape "
            "over minus three to three."
        ),
        "expect_guide": "plot",
        "rubric": [
            "the lead-in pushes the writer to open a guide before drawing",
            "the menu makes 'plot' the obvious pick for numeric curves and graphs",
        ],
        "guard": True,
    },
    {
        "input": "scatter of height vs weight with a fit line",
        "transcript": (
            "Height and weight move together — taller people tend to weigh "
            "more, though with plenty of spread. A scatter with a fitted line "
            "shows both the trend and the noise at once."
        ),
        "expect_guide": "plot",
        "rubric": [
            "the menu steers data-and-fit pictures (scatter, regression) to 'plot'",
        ],
    },
    {
        "input": "flowchart of the checkout process",
        "transcript": (
            "Checkout runs cart review, then shipping, then payment, then "
            "confirmation — with a retry loop back from a failed payment. A "
            "flowchart makes that branching obvious."
        ),
        "expect_guide": "mermaid",
        "rubric": [
            "the menu steers structural diagrams (flowcharts, processes) to 'mermaid'",
        ],
    },
    {
        "input": "sequence diagram of the API handshake",
        "transcript": (
            "The handshake is three exchanges: the client sends hello with its "
            "nonce, the server answers with its certificate and cipher choice, "
            "then both sides confirm keys. A sequence diagram shows who sends "
            "what, in order."
        ),
        "expect_guide": "mermaid",
        "rubric": [
            "the menu steers ordered actor-to-actor exchanges to 'mermaid'",
        ],
    },
    # ---- borderline set (added 2026-07-11) — requests whose surface wording
    # pulls toward the WRONG modality; these are where a better lead-in/summary
    # can actually move the pick, i.e. where refinement headroom lives.
    {
        "input": "bar chart of quarterly sales by region",
        "transcript": (
            "Sales climbed through Q2 and flattened after; what matters is the "
            "regional split, and side-by-side bars make that comparison "
            "instant."
        ),
        "expect_guide": "mermaid",
        "rubric": [
            "bar/line charts belong to 'mermaid' in this registry even though "
            "they sound numeric — the menu must make that routing unmissable",
        ],
    },
    {
        "input": "diagram of y = sin(x) from 0 to 2π",
        "transcript": (
            "Sine starts at zero, peaks at π/2, crosses back down through π, "
            "and completes the cycle at 2π — let me draw the actual wave so "
            "the shape is visible."
        ),
        "expect_guide": "plot",
        "rubric": [
            "a numeric curve routes to 'plot' even when the user says "
            "'diagram' — content over surface wording",
        ],
    },
    {
        "input": "show how the shell, persona, and knowledge services talk to each other",
        "transcript": (
            "The shell fronts everything — requests hit it first and it "
            "proxies to persona or knowledge; persona also calls knowledge "
            "directly. A picture of who calls whom anchors this."
        ),
        "expect_guide": "mermaid",
        "rubric": [
            "structural who-talks-to-whom requests route to 'mermaid' even "
            "with no chart/diagram keyword in the request",
        ],
    },
    {
        "input": "visualize how reaction rate changes with temperature from these measurements",
        "transcript": (
            "The rate roughly doubles every ten degrees — plotting the "
            "measured points against temperature makes the exponential trend "
            "obvious."
        ),
        "expect_guide": "plot",
        "rubric": [
            "measured data against a variable routes to 'plot' from generic "
            "verbs like 'visualize' — it is a data-and-fit picture",
        ],
    },
]


__all__ = ["SCENARIOS"]
