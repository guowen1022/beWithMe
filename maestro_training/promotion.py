"""STUB — promotion gates per SPEC §13.4.

A learned trigger gate may only replace the heuristic when, on
held-out log replay, it shows:
  - margin δ on engagement quality
  - no regression on silence rate
  - no regression on deletion rate
  - declared-mattered count >= heuristic's

Implementation lands together with the first trainable gate variant.
"""
