"""STUB — Differential-Privacy boundary for cross-user aggregation.

Per SPEC §14.2 / §15: training data must NOT carry per-user content
across the boundary. This module will host the noise + clipping
utilities for cross-user gradient aggregation when cross-user
training begins.

Out of scope for the PR-8 scaffold — single-user replay (the
replay_user path) doesn't cross the boundary.
"""
