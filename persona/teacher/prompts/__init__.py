"""Teacher prompt builders — one module per scenario.

- `answer.build(...)`  — user-question scenario (was prompt_v2)
- `reflect.build(...)` — perception-event scenario (replaces the
                         [auto-trigger] hack in triggers.py)

Shared building blocks live in `parts`, `canvas_renderer`,
`learner_state`, `preferences_block`. `skills` re-exports the workshop
loader for ergonomic imports inside this package.
"""
from __future__ import annotations

from persona.teacher.prompts import answer, reflect
from persona.teacher.prompts.parts import (
    PromptParts,
    build_history_messages,
    clean_answer_for_history,
    parse_title,
)

__all__ = [
    "answer",
    "reflect",
    "PromptParts",
    "parse_title",
    "clean_answer_for_history",
    "build_history_messages",
]
