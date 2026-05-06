"""LLM-call observability hook.

Provides a thin pubsub channel for "the teacher (or any persona) just
ran an LLM call." Used by the LLM facade to broadcast TeacherThinking
events to the developer debug panel.

Layer note: this module lives in `infra/` so the `infra/model/llm.py`
facade can import it without crossing into `services/`. The persona
service registers a callback at startup that pipes events to its SSE
fan-out (`enqueue_for_user`).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

# Set by `services.persona.main` at startup. None until then.
_emit: Optional[Callable[[UUID, Any], Awaitable[None]]] = None


def register_emit(fn: Callable[[UUID, Any], Awaitable[None]]) -> None:
    """Register the SSE fan-out function. Called once per process at
    startup. Subsequent calls overwrite (useful for tests)."""
    global _emit
    _emit = fn


async def emit_thinking(user_id: Optional[UUID], event: Any) -> None:
    """Send a TeacherThinking event to whichever sink is registered.

    Silently no-ops when no sink is registered (e.g. running a one-off
    script) or when the sink raises (observability never breaks the
    underlying call).
    """
    if _emit is None or user_id is None:
        return
    try:
        await _emit(user_id, event)
    except Exception:  # noqa: BLE001 — observability must never bubble up
        pass


def reset_for_tests() -> None:
    """Drop the registered emitter. Used by tests for isolation."""
    global _emit
    _emit = None


__all__ = ["register_emit", "emit_thinking", "reset_for_tests"]
