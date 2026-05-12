"""Research skill set — generic web-investigation recipes that any persona
or agent can record and replay.

Concept: a "recipe" captures the (intent, host, parameterized tool-call
sequence, recorded ARIA refs) tuple from a successful Lane R run. Future
research requests with a matching intent + same host can replay the
recipe instead of re-deriving the plan, dropping a ~90 s LLM-driven
investigation to a ~5–10 s deterministic replay + one synthesis call.

This is generic infrastructure — it lives outside `persona/` so the
teacher, helper, engineer, etc. can all benefit from the same library.
Persona-flavored bits (synthesis voice, delivery channel) live in the
calling persona's trigger module.

Storage: `data/research/<user_id>/recipes/<recipe_id>.json` (one file
per recipe; per-user; no DB).
"""
from __future__ import annotations

from workshop.research import (  # noqa: F401 — re-exports
    per_host_skills,
    recipe_parameterize,
    recipe_runner,
    recipe_store,
    recipes,
)
from workshop.research.per_host_skills import PerHostSkill  # noqa: F401
from workshop.research.recipe_store import ResearchRecipe  # noqa: F401

__all__ = [
    "PerHostSkill",
    "ResearchRecipe",
    "per_host_skills",
    "recipe_parameterize",
    "recipe_runner",
    "recipe_store",
    "recipes",
]
