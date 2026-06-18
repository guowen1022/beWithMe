"""Tool-calling primitives shared across providers.

A `ToolSpec` is what callers pass into `stream_with_tools`: a name, a
human-readable description for the LLM, a JSON Schema for the parameters,
and an `executor` coroutine that the tool loop will invoke once the LLM
emits a matching tool call.

The executor signature is `(args: dict) -> Awaitable[str]`. The string is
fed back to the LLM as the tool result. Keep results compact — the LLM
re-reads them on every subsequent turn.

Provider modules translate `ToolSpec` into their wire format:
  * deepseek (OpenAI-style):   {type: "function", function: {name, description, parameters}}
  * minimax (Anthropic-style): {name, description, input_schema}
  * fake:                      script of canned tool_call events for tests

Streaming yield shape — every provider must produce these dicts:
  {"kind": "delta", "text": "..."}              — assistant text chunk
  {"kind": "tool_call", "id": "...",
   "name": "...", "arguments": {...}}            — one fully-assembled call
  {"kind": "done", "text": "...", "usage": {...},
   "stop_reason": "tool_use" | "end_turn"}       — turn complete
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict


ToolExecutor = Callable[[Dict[str, Any]], Awaitable[str]]


class ToolDomain(str, Enum):
    """The area of verbs a tool belongs to (≈ its owning package). A persona's
    `CapabilityGrant` is a set of these — a persona may select a tool iff the
    tool's domain is in its grant. See `infra/model/authz.py` and
    ARCHITECTURE.md §4.4."""
    COMMON = "common"      # tools/
    CANVAS = "canvas"      # workshop/canvas/tools/
    TEACHER = "teacher"    # persona/teacher/tools/
    APP = "app"            # persona/app_operator/tools/
    ENGINEER = "engineer"  # agents/frontend_engineer/


@dataclass
class ToolSpec:
    name: str
    description: str
    params_schema: Dict[str, Any]   # JSON Schema for the function's args
    executor: ToolExecutor
    # Which persona-domain owns this verb. Authorization (infra/model/authz.py)
    # lets a persona select a tool iff its domain is in the persona's grant.
    # Defaults to COMMON so generic `tools/` verbs need no change.
    domain: ToolDomain = ToolDomain.COMMON

    def to_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }

    def to_anthropic(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.params_schema,
        }


__all__ = ["ToolSpec", "ToolExecutor", "ToolDomain"]
