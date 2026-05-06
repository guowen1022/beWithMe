"""Per-scenario context assembly.

- `answer.assemble(body, user_id, db, client)` — full RAG + history pipeline
- `reflect.assemble(user_id, events)` — minimal canvas + concepts pipeline

Both return `TeacherContext(parts, prior_messages)`. The agent dispatch
selects the right `assemble` and the tool loop consumes the result the
same way regardless.
"""
from __future__ import annotations

from persona.teacher.contexts import answer, reflect
from persona.teacher.contexts.answer import TeacherContext

__all__ = ["answer", "reflect", "TeacherContext"]
