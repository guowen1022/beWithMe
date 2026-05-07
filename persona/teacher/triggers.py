"""Event-driven teacher triggers — perception → reflect prompt.

Subscribes to the perception cache. On each event type the teacher
gets a fresh reflect-scenario turn:

- BlockCompletedEvent → an interactive surface finished (upload done,
  form submitted, etc.). High-signal; the teacher should usually act.
- BlockChangeEvent    → an ambient block-state update (PDF page change,
  scroll, viewport refresh). Low-signal; the teacher mostly observes.
- VoiceEvent          → a voice utterance played on the user's speakers.
  Mid-signal; sometimes worth a reaction.
- UserSpeechEvent     → ambient mic captured a user phrase (target_persona
  must equal "teacher" or the event is dropped). Mid-signal; the teacher
  mostly stays silent (see skills/respond_to_speech.md).

Per-event-type runtime budgets keep cost in line:

    completion   → 10s cooldown, 1500 max_tokens
    change       → 30s cooldown,  600 max_tokens (heavily coalesced)
    voice        →  5s cooldown,  800 max_tokens
    user_speech  →  2s cooldown,  700 max_tokens (silence-by-default,
                                                  dedupe identical text)

All event types feed the same `prompts.reflect.build` — there is no
per-prompt distinction. The events list carries the type so the LLM
can weight a `completed` event more heavily than a `changed` one.

Per-user state machine per event class:

  idle      — no recent fire. On event: fire immediately, transition to "firing".
  firing    — turn in progress. On event: enqueue.
  cooldown  — last fire <COOLDOWN_S ago. On event: enqueue; the cooldown
              timer's tail will fire one more turn with everything buffered.

Output of each turn:
  * tool calls (mount block, push content, speak) take effect via the
    existing tool executors.
  * any text the teacher emits is sent to the debug channel as a
    `teacher-thinking` SSE event for the user's debug panel; it does
    NOT stream into a chat panel (no user message → no chat turn).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union
from uuid import UUID

from infra.perception import (
    BlockChangeEvent,
    BlockCompletedEvent,
    UserSpeechEvent,
    VoiceEvent,
    subscribe,
    unsubscribe,
)


# This persona's name. UserSpeechEvents whose `target_persona` doesn't
# match are dropped at classify-time so they never burn the teacher's
# cooldown budget. Future personas register their own orchestrator with
# its own name.
_PERSONA_NAME = "teacher"


# ---- per-event-type runtime budgets --------------------------------------


@dataclass(frozen=True)
class _Budget:
    """Cooldown + token cap for one event class."""
    cooldown_s: float
    max_tokens: int
    trigger_label: str


_BUDGETS: Dict[str, _Budget] = {
    "completed":   _Budget(cooldown_s=10.0, max_tokens=1500, trigger_label="block-completed"),
    "change":      _Budget(cooldown_s=30.0, max_tokens=600,  trigger_label="canvas-changed"),
    "voice":       _Budget(cooldown_s=5.0,  max_tokens=800,  trigger_label="voice"),
    "user_speech": _Budget(cooldown_s=2.0,  max_tokens=700,  trigger_label="user-speech"),
}


# ---- per-user trigger state ---------------------------------------------


@dataclass
class _BucketState:
    """One queue + cooldown timer per (user, event-type) bucket."""
    queue: List[Any] = field(default_factory=list)  # PerceptionEventSummary list
    firing: bool = False
    last_fire_at: float = 0.0
    cooldown_task: Optional[asyncio.Task] = None


@dataclass
class _UserState:
    buckets: Dict[str, _BucketState] = field(default_factory=lambda: {
        "completed":   _BucketState(),
        "change":      _BucketState(),
        "voice":       _BucketState(),
        "user_speech": _BucketState(),
    })


_users: Dict[str, _UserState] = {}


def _state_for(user_id: UUID) -> _UserState:
    key = str(user_id)
    s = _users.get(key)
    if s is None:
        s = _UserState()
        _users[key] = s
    return s


# ---- listener wiring ----------------------------------------------------


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


_AnyEvent = Union[BlockChangeEvent, BlockCompletedEvent, VoiceEvent, UserSpeechEvent]


def _classify(event: _AnyEvent) -> Optional[str]:
    """Map an event to its bucket key, or None to drop it."""
    if isinstance(event, BlockCompletedEvent):
        return "completed"
    if isinstance(event, BlockChangeEvent):
        return "change"
    if isinstance(event, VoiceEvent):
        return "voice"
    if isinstance(event, UserSpeechEvent):
        # Persona-dispatch filter: only react to events targeted at us.
        # Future personas drop the events that aren't theirs at this seam.
        if event.target_persona != _PERSONA_NAME:
            return None
        return "user_speech"
    return None


def _summarise_event(
    event: _AnyEvent,
    bucket: str,
) -> Any:
    """Convert a cache event to a `PerceptionEventSummary` for the
    reflect prompt. Imported lazily to avoid a startup cycle."""
    from persona.teacher.prompts.reflect import PerceptionEventSummary

    if bucket == "voice":
        u = event.utterance
        return PerceptionEventSummary(
            event_type="voice",
            block_id=None,
            state_kind=None,
            content=u.text,
            extra=None,
        )

    if bucket == "user_speech":
        u = event.utterance
        extra: Dict[str, Any] = {}
        if u.language:
            extra["language"] = u.language
        if u.audio_duration_s is not None:
            extra["duration_s"] = round(u.audio_duration_s, 2)
        return PerceptionEventSummary(
            event_type="user_speech",
            block_id=None,
            state_kind=None,
            content=u.text,
            extra=extra or None,
        )

    state = event.state
    return PerceptionEventSummary(
        event_type=bucket,  # "completed" or "change"
        block_id=event.block_id,
        state_kind=state.kind if state else None,
        content=(state.content if state else None),
        extra=(state.extra if state else None),
    )


async def _on_perception_event(
    event: _AnyEvent,
) -> None:
    """Cache-listener entry point. Routes by event type to the right bucket."""
    bucket = _classify(event)
    if bucket is None:
        return

    user_id = event.user_id
    state = _state_for(user_id)
    bucket_state = state.buckets[bucket]
    summary = _summarise_event(event, bucket)
    bucket_state.queue.append(summary)

    print(
        f"[teacher.triggers] queued {bucket}: user={user_id} "
        f"firing={bucket_state.firing} qsize={len(bucket_state.queue)}",
        flush=True,
    )

    loop = asyncio.get_event_loop()
    now = loop.time()
    budget = _BUDGETS[bucket]

    if bucket_state.firing:
        return

    elapsed = now - bucket_state.last_fire_at
    if bucket_state.last_fire_at > 0 and elapsed < budget.cooldown_s:
        if bucket_state.cooldown_task is None or bucket_state.cooldown_task.done():
            wait = budget.cooldown_s - elapsed
            print(
                f"[teacher.triggers] {bucket} cooldown active ({elapsed:.1f}s "
                f"elapsed); trailing fire in {wait:.1f}s",
                flush=True,
            )
            bucket_state.cooldown_task = loop.create_task(
                _fire_after(user_id, bucket, wait)
            )
        return

    print(
        f"[teacher.triggers] firing {bucket} immediately for user={user_id}",
        flush=True,
    )
    bucket_state.firing = True
    loop.create_task(_run_turn(user_id, bucket))


async def _fire_after(user_id: UUID, bucket: str, delay_s: float) -> None:
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    state = _state_for(user_id)
    bucket_state = state.buckets[bucket]
    if bucket_state.firing:
        return
    if not bucket_state.queue:
        return
    bucket_state.firing = True
    await _run_turn(user_id, bucket)


async def _run_turn(user_id: UUID, bucket: str) -> None:
    """Drain the queue + run one teacher turn for this bucket."""
    state = _state_for(user_id)
    bucket_state = state.buckets[bucket]
    events = list(bucket_state.queue)
    bucket_state.queue.clear()
    # For user_speech, the user often repeats themselves while waiting on
    # a response ("hello hello hello"). Collapsing duplicates keeps the
    # batched turn focused on the latest unique phrase rather than
    # responding to a stack of the same utterance.
    if bucket == "user_speech" and len(events) > 1:
        seen: dict[str, int] = {}
        for i, e in enumerate(events):
            key = (e.content or "").strip().lower()
            if key:
                seen[key] = i
        events = [events[i] for i in sorted(seen.values())]
    try:
        await _execute_turn(user_id, bucket, events)
    except Exception as e:
        print(
            f"[teacher.triggers] {bucket} turn failed for user {user_id}: {e}",
            flush=True,
        )
    finally:
        loop = asyncio.get_event_loop()
        bucket_state.last_fire_at = loop.time()
        bucket_state.firing = False
        if bucket_state.queue and (
            bucket_state.cooldown_task is None or bucket_state.cooldown_task.done()
        ):
            bucket_state.cooldown_task = loop.create_task(
                _fire_after(user_id, bucket, _BUDGETS[bucket].cooldown_s)
            )


# ---- turn execution -----------------------------------------------------


async def _execute_turn(
    user_id: UUID,
    bucket: str,
    events: List[Any],
) -> None:
    """Drive run_teacher_tool_loop with the reflect-scenario prompt.

    No synthesized question, no answer-pipeline RAG. Events feed
    directly into `prompts.reflect.build` via `contexts.reflect.assemble`.
    """
    # Late imports to avoid module-import cycles at startup.
    from persona.teacher.contexts.reflect import assemble as assemble_reflect
    from persona.teacher.tools import build_tools as build_teacher_tools
    from persona.teacher.tools.loop import run as run_teacher_tool_loop
    from services.persona.routers.dynamic import enqueue_for_user
    from infra.contracts.ui import TeacherThinking

    budget = _BUDGETS[bucket]

    # Compact summary line for the debug panel.
    summary_lines = []
    for e in events:
        bits = [f"[{e.event_type}]"]
        if e.block_id:
            bits.append(f"block={e.block_id}")
        if e.state_kind:
            bits.append(f"kind={e.state_kind}")
        if e.content:
            short = (e.content or "").strip().replace("\n", " ")
            if len(short) > 60:
                short = short[:57] + "…"
            bits.append(f"content={short!r}")
        summary_lines.append(" ".join(bits))
    summary = "\n".join(summary_lines)

    await enqueue_for_user(user_id, TeacherThinking(
        phase="start",
        trigger=budget.trigger_label,
        summary=summary,
    ))

    teacher_tools = build_teacher_tools(user_id)
    text_chunks: List[str] = []
    tool_calls_seen: List[Dict[str, Any]] = []
    error_text: Optional[str] = None

    try:
        ctx = await assemble_reflect(user_id, events)

        async for evt in run_teacher_tool_loop(
            static_system=ctx.parts.static_system,
            static_user_passage=ctx.parts.static_user_passage,
            dynamic_user=ctx.parts.dynamic_user,
            prior_messages=ctx.prior_messages,
            tools=teacher_tools,
            max_tokens=budget.max_tokens,
            purpose="reflect",
            user_id=user_id,
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
        import traceback
        print(f"[teacher.triggers] _execute_turn ({bucket}) error: {e}", flush=True)
        traceback.print_exc()
        error_text = f"{type(e).__name__}: {e}"
    finally:
        final_text = "".join(text_chunks).strip()
        if error_text:
            final_text = (final_text + "\n\n[error] " + error_text).strip()
        elif not final_text and not tool_calls_seen:
            # Teacher reviewed the perception update and chose to act —
            # by doing nothing. Surface that explicitly so the debug panel
            # can distinguish "received and decided silence" from "didn't
            # run at all" / "error swallowed". Especially relevant for
            # the user_speech bucket where silence is the policy default
            # (see persona/teacher/skills/respond_to_speech.md).
            final_text = "(silent — no response chosen)"
        await enqueue_for_user(user_id, TeacherThinking(
            phase="end",
            trigger=budget.trigger_label,
            summary=summary,
            text=final_text[:500],
            tool_calls=tool_calls_seen,
        ))


# ---- test hooks ---------------------------------------------------------


def _reset_for_tests() -> None:
    """Clear all per-user state. Used by tests for isolation."""
    for state in _users.values():
        for bucket_state in state.buckets.values():
            if bucket_state.cooldown_task is not None and not bucket_state.cooldown_task.done():
                bucket_state.cooldown_task.cancel()
    _users.clear()


# Backwards-compat for tests that imported COOLDOWN_S directly.
COOLDOWN_S = _BUDGETS["completed"].cooldown_s
MAX_TOKENS = _BUDGETS["completed"].max_tokens
