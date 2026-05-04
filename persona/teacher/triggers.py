"""Event-driven teacher triggers.

Subscribes to the perception cache. When a block fires a
`BlockCompletedEvent`, the orchestrator wakes the teacher's tool loop —
the same loop a normal user-asked turn uses — with a synthesized
`dynamic_user` message that summarises the events.

Per-user cooldown: at most one teacher run per `COOLDOWN_S` seconds. Events
that arrive during a fire OR during the cooldown window are buffered;
they fan into a single trailing fire when the cooldown expires.

State machine per user:

  idle      — no recent fire. On event: fire immediately, transition to "firing".
  firing    — turn in progress. On event: enqueue.
  cooldown  — last fire <COOLDOWN_S ago. On event: enqueue; the cooldown
              timer's tail will fire one more turn with everything buffered.

The orchestrator never blocks the cache producer — fires happen on
detached asyncio tasks. Output of the teacher's tool loop:
  * tool calls (mount block, push content, speak) take effect via the
    existing tool executors.
  * any text the teacher emits is sent to the new debug channel as a
    `teacher-thinking` SSE event for the user's debug panel; it does
    NOT stream into a chat panel (no user message → no chat turn).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from infra.perception import (
    BlockCompletedEvent,
    BlockChangeEvent,
    VoiceEvent,
    subscribe,
    unsubscribe,
)


# Default cooldown — overridable by the persona service at startup.
COOLDOWN_S = 10.0

# Per-turn token cap — tighter than the user-asked path because triggers
# fire without the user explicitly waiting.
MAX_TOKENS = 1500


# ---- per-user trigger state ----------------------------------------------


@dataclass
class _UserState:
    queue: List[BlockCompletedEvent] = field(default_factory=list)
    firing: bool = False
    last_fire_at: float = 0.0          # event-loop time
    cooldown_task: Optional[asyncio.Task] = None


_users: Dict[str, _UserState] = {}


def _state_for(user_id: UUID) -> _UserState:
    key = str(user_id)
    s = _users.get(key)
    if s is None:
        s = _UserState()
        _users[key] = s
    return s


# ---- listener wiring -----------------------------------------------------


_unsubscribe_handle: Optional[Any] = None


def install() -> None:
    """Subscribe the orchestrator to the perception cache. Idempotent."""
    global _unsubscribe_handle
    if _unsubscribe_handle is not None:
        return
    _unsubscribe_handle = subscribe(_on_perception_event)


def uninstall() -> None:
    """Drop the subscription. Used by tests / shutdown."""
    global _unsubscribe_handle
    if _unsubscribe_handle is None:
        return
    try:
        _unsubscribe_handle()
    except Exception:
        pass
    _unsubscribe_handle = None


async def _on_perception_event(event: Union[BlockChangeEvent, BlockCompletedEvent, VoiceEvent]) -> None:
    """Cache-listener entry point. We only care about completion edges."""
    if not isinstance(event, BlockCompletedEvent):
        return
    state = _state_for(event.user_id)
    state.queue.append(event)
    print(
        f"[teacher.triggers] queued completion: user={event.user_id} "
        f"block={event.block_id} firing={state.firing} qsize={len(state.queue)}",
        flush=True,
    )

    loop = asyncio.get_event_loop()
    now = loop.time()

    if state.firing:
        # Already running a turn; the queue we just appended to will
        # drain on the next fire.
        return

    elapsed = now - state.last_fire_at
    if state.last_fire_at > 0 and elapsed < COOLDOWN_S:
        # Cooldown active. Schedule a trailing fire if not already.
        if state.cooldown_task is None or state.cooldown_task.done():
            wait = COOLDOWN_S - elapsed
            print(
                f"[teacher.triggers] cooldown active ({elapsed:.1f}s elapsed); "
                f"trailing fire in {wait:.1f}s",
                flush=True,
            )
            state.cooldown_task = loop.create_task(_fire_after(event.user_id, wait))
        return

    # Idle path: fire immediately.
    print(f"[teacher.triggers] firing immediately for user={event.user_id}", flush=True)
    state.firing = True
    loop.create_task(_run_turn(event.user_id))


async def _fire_after(user_id: UUID, delay_s: float) -> None:
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    state = _state_for(user_id)
    if state.firing:
        return  # someone else picked it up
    if not state.queue:
        return
    state.firing = True
    await _run_turn(user_id)


async def _run_turn(user_id: UUID) -> None:
    """Drain the queue + run one teacher turn. Always clears `firing` and
    sets `last_fire_at` on exit so cooldown logic stays consistent."""
    state = _state_for(user_id)
    events = list(state.queue)
    state.queue.clear()
    try:
        await _execute_turn(user_id, events)
    except Exception as e:
        print(f"[teacher.triggers] turn failed for user {user_id}: {e}", flush=True)
    finally:
        loop = asyncio.get_event_loop()
        state.last_fire_at = loop.time()
        state.firing = False
        # If new events arrived during the turn, schedule a trailing fire
        # at cooldown-end.
        if state.queue and (state.cooldown_task is None or state.cooldown_task.done()):
            state.cooldown_task = loop.create_task(_fire_after(user_id, COOLDOWN_S))


# ---- turn execution ------------------------------------------------------


def _summarise_events(events: List[BlockCompletedEvent]) -> str:
    """Compact, deterministic event summary for the synthetic user message."""
    lines: List[str] = []
    for e in events:
        s = e.state
        extra = ""
        if s.extra:
            try:
                extra = f" extra={json.dumps(s.extra)}"
            except Exception:
                extra = f" extra={s.extra!r}"
        lines.append(
            f"- block_id={e.block_id} kind={s.kind} content={s.content!r}{extra}"
        )
    return "\n".join(lines)


async def _execute_turn(user_id: UUID, events: List[BlockCompletedEvent]) -> None:
    """Drive run_teacher_tool_loop with a synthesized message describing
    the completion events. The persona's existing tool stack runs as
    usual; tool calls take effect via their executors."""
    # Late imports to avoid module-import cycles at startup.
    from infra.db import async_session
    from persona.teacher import assemble_context
    from persona.teacher.schemas import AskRequest
    from persona.teacher.silicon_brain_client import SiliconBrainClient
    from persona.teacher.tools import build_tools as build_teacher_tools
    from persona.teacher.tools.loop import run as run_teacher_tool_loop
    from services.persona.routers.dynamic import enqueue_for_user
    from infra.contracts.ui import TeacherThinking

    summary = _summarise_events(events)
    # The teacher's prompt frames this as "auto-trigger" so the LLM knows
    # there's no user message to respond to — it should *act*, not narrate.
    synthetic_message = (
        "[auto-trigger] One or more blocks just finished interacting with "
        "the user. Decide whether the user's next step is now obvious "
        "(e.g., a doc upload finished → mount a reader for it) and act "
        "via your tools. If nothing actionable, do nothing.\n\n"
        f"Completed events:\n{summary}"
    )

    body = AskRequest(
        question=synthetic_message,
        prompt_version="v2",
    )

    # Notify any open SSE consumers that the trigger is firing — useful
    # for the debug "llm thinking" panel.
    await enqueue_for_user(user_id, TeacherThinking(
        phase="start",
        trigger="block-completed",
        summary=summary,
    ))

    teacher_tools = build_teacher_tools(user_id)
    text_chunks: List[str] = []
    tool_calls_seen: List[Dict[str, Any]] = []
    error_text: Optional[str] = None

    try:
        async with async_session() as db:
            client = SiliconBrainClient()
            try:
                ctx = await assemble_context(body, user_id, db, client)
            finally:
                try:
                    await client.aclose()
                except Exception:
                    pass

        async for evt in run_teacher_tool_loop(
            static_system=ctx.parts.static_system,
            static_user_passage=ctx.parts.static_user_passage,
            dynamic_user=ctx.parts.dynamic_user,
            prior_messages=ctx.prior_messages,
            tools=teacher_tools,
            max_tokens=MAX_TOKENS,
        ):
            kind = evt.get("kind")
            if kind == "delta":
                text_chunks.append(evt.get("text", ""))
            elif kind == "tool_call":
                tool_calls_seen.append({
                    "name": evt.get("name"),
                    "arguments": evt.get("arguments") or {},
                })
    except Exception as e:
        # The outer _run_turn also catches, but we need to still ship the
        # `end` event so the frontend's "running…" pill closes. Log the
        # error inline so it shows up in the panel for debugging.
        import traceback
        print(f"[teacher.triggers] _execute_turn error: {e}", flush=True)
        traceback.print_exc()
        error_text = f"{type(e).__name__}: {e}"
    finally:
        final_text = "".join(text_chunks).strip()
        if error_text:
            final_text = (final_text + "\n\n[error] " + error_text).strip()
        await enqueue_for_user(user_id, TeacherThinking(
            phase="end",
            trigger="block-completed",
            summary=summary,
            text=final_text[:500],
            tool_calls=tool_calls_seen,
        ))


# ---- test hooks ----------------------------------------------------------


def _reset_for_tests() -> None:
    """Clear all per-user state. Used by tests for isolation."""
    for state in _users.values():
        if state.cooldown_task is not None and not state.cooldown_task.done():
            state.cooldown_task.cancel()
    _users.clear()
