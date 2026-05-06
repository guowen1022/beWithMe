"""Teacher Agent — one agent that reads the silicon brain and teaches.

The teacher:
  1. Listens to UI events (passage, question, highlights)
  2. Reads learner context from the silicon brain
  3. Responds via the UI (streamed answer)
  4. Feeds learnings back to the brain builder
"""

from persona.teacher.agent import TeacherContext, assemble_context
from persona.teacher.prompts.answer import build as build_answer_prompt
from persona.teacher.prompts.parts import PromptParts, parse_title

__all__ = [
    "assemble_context",
    "TeacherContext",
    "PromptParts",
    "build_answer_prompt",
    "parse_title",
]
