"""Re-export shim.

The generic tool-execution loop moved to `infra/model/agent_loop.py` so
multiple personas (teacher, app_operator, …) can share it without crossing
the persona-to-persona import boundary (ARCHITECTURE.md invariant #4 — a
persona may not import another persona's internals). The loop itself only
ever depended on `infra.*`, so it belongs in the leaf.

Teacher code keeps importing `persona.teacher.tools.loop.run`; this module
forwards it unchanged.
"""
from infra.model.agent_loop import run

__all__ = ["run"]
