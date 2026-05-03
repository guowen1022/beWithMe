"""Teacher's tool surface — what the teacher LLM can ask the system to do.

`build_tools(user_id)` returns the per-request `ToolSpec` list, each with
its `executor` already bound to `user_id`. The execution loop in
`persona/teacher/tools/loop.py` consumes that list directly.
"""
from persona.teacher.tools.manifest import build_tools

__all__ = ["build_tools"]
