"""STUB — listwise + binary losses for the trigger gate (PR-8 follow-up).

Listwise (K>1): picked candidate = explicit positive; unpicked = implicit
negatives; "all expired/dismissed" = negative on the whole ACT decision.

Binary (K=1): standard ACT vs SILENCE outcome.

Implementation lands once we have enough logged ACT decisions to
estimate the loss gradient stably. Until then, replay.summarise gives
the Phase-0 baseline.
"""
