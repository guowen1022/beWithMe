"""Inter-source blend — merge every persona's cards into one ranked feed.

Per premise 4: personas own *intra-source* ranking (`intra_rank`); the
Maestro owns the *inter-source* blend. The blended score multiplies a card's
own rank by its persona's saturation weight, so an over-used persona's whole
column dims at once while another persona's cards rise — without reordering
anything *within* a persona.

`blended_score = intra_rank * persona_weight(source_persona)`

With one live persona and the Phase-0 saturation stub (weight = 1.0) this is a
straight `intra_rank` sort; the machinery is source-agnostic, so dropping in a
second persona "just works."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RankedCard:
    card: Any            # FeedCandidateDTO
    blended_score: float
    persona_weight: float


def rank(cards: list, weights: dict[str, float]) -> list[RankedCard]:
    """Sort cards by blended score (desc). `weights` maps source_persona →
    weight (missing personas default to 1.0). Ties keep a stable order, then
    break by newest `created_at` so fresher cards edge ahead."""
    ranked: list[RankedCard] = []
    for c in cards:
        w = weights.get(getattr(c, "source_persona", ""), 1.0)
        ranked.append(RankedCard(
            card=c,
            blended_score=float(getattr(c, "intra_rank", 0.5)) * w,
            persona_weight=w,
        ))

    def _created_key(rc: RankedCard):
        ts = getattr(rc.card, "created_at", None)
        return ts.isoformat() if ts is not None else ""

    ranked.sort(key=lambda rc: (rc.blended_score, _created_key(rc)), reverse=True)
    return ranked
