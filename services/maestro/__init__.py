"""Maestro sidecar — the long-instance reasoning loop (PR-4).

Subscribes to event-stream signals worth gating on (engagement_ended,
captures, due followups, periodic ticks) and decides ACT vs SILENCE per
SPEC §6. On ACT, generates 1..K diverse candidates (SPEC §6.1) and
emits `maestro_long.kickoff_decision` into the per-user event stream.
The agent reads kickoffs in PR-5; the cache + short instance arrive in
PR-5/PR-6.
"""
