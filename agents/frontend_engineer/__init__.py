"""frontend_engineer — produces JS block sources for the dynamic UI.

Public surface:
    from agents.frontend_engineer import build

V1 is a stub that returns a hardcoded "hello" block. The walking skeleton
proves the delegation path (teacher → tool → agent → SSE → browser eval →
back-channel push) before the LLM streaming + retry loop is ported from
block-canvas/lib/streamCommand.ts.
"""
from agents.frontend_engineer.build import build

__all__ = ["build"]
