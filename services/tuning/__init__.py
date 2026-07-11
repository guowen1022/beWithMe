"""Tuning sidecar — beWithMe's skillforge host face (:BASE_PORT+8).

Serves the real eval endpoint skillforge's refine loop RPCs
(`POST /eval {body, config, scenario} → {ok, quality, outcome}`) and
self-registers with the local skillforge instance on boot. Offline-only:
nothing on the user request path calls this sidecar.
"""
