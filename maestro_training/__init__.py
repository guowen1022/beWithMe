"""Maestro trigger-gate training (PR-8 scaffold — Phase 1).

Read-only over the per-user event stream + the views shipped in
PR-1..PR-7. NO writes back to the live runtime. The replay harness
loads kickoff_log + inbox_interaction_log + engagement_log, joins
them on kickoff_event_id, derives a per-kickoff outcome, and
computes the Phase-0 baseline metrics that the eventual learned gate
must beat.

Lives at the project root (not under `services/`, `persona/`, or
`silicon_brain/`) because training pipelines have a different deploy
shape than the runtime — they're CI/batch jobs, not sidecars. Per
ARCHITECTURE.md the runtime layers don't import this package.

PR-8 incremental scope (this file):
  - `replay.py` — read views, join, compute Phase-0 outcome metrics
    (tap rate, expiry rate, engagement-quality stub).
  - `losses.py` — STUB. Listwise + binary losses land in PR-8 follow-up
    once the IPS plumbing in PR-4 is exercised by real-log volume.
  - `ips.py` — STUB. Off-policy correction.
  - `dp.py` — STUB. Differential-privacy boundary for cross-user
    aggregation (SPEC §14.2 / §15).
  - `promotion.py` — STUB. Promotion gates per SPEC §13.4.
"""
