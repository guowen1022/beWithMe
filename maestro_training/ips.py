"""STUB — Inverse-Propensity-Score correction for off-policy training.

The Phase-0 gate is heuristic. Every kickoff_decision body carries
`propensity` (the heuristic's confidence in the decision). Phase 1+
trains a learned classifier on logged outcomes; IPS reweights the
loss by 1/propensity so the learned policy isn't biased toward the
states the heuristic happened to ACT/SILENCE on.

Implementation lands when replay.summarise shows enough propensity
spread to make IPS estimators stable.
"""
