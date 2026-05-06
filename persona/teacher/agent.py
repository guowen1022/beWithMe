"""Teacher agent — context assembly entry points.

The heavy work moved to `persona.teacher.contexts.*`. This module
preserves the historical public API:

  from persona.teacher import assemble_context, TeacherContext

`assemble_context` is the answer-scenario assembler. Reactive paths
(triggers) call `contexts.reflect.assemble` directly — they don't use
the answer pipeline anymore.
"""
from __future__ import annotations

from persona.teacher.contexts.answer import TeacherContext, assemble as assemble_context

__all__ = ["assemble_context", "TeacherContext"]
