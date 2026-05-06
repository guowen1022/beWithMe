"""Convenience re-export of `workshop.load_skill`.

The workshop loader is the single source of truth for resolving
qualified skill names like `teacher/teaching_principle` and
`workshop/canvas/grid`. This module exists so callers inside the
teacher persona can `from persona.teacher.prompts.skills import
load_skill` without reaching across the repo.
"""
from __future__ import annotations

from workshop import load_skill, register_root

__all__ = ["load_skill", "register_root"]
