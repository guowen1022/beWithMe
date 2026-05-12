"""Event-driven teacher triggers — perception → two lanes.

The persona has two execution lanes:

  Lane A — User-facing.
    One task per user, cancellable, 500 ms debounced. Triggered by
    `UserSpeechEvent` (target=teacher) and external `VoiceEvent`.
    Calls `run_teacher_tool_loop` with `lane="user_facing"` and
    `max_iterations=1` so the LLM gets one chance to speak/act/silence
    instead of running a multi-step tool loop. New user_speech preempts
    a running Lane A task — the running task is cancelled, events are
    merged, and a fresh turn fires after the debounce.

  Lane B — Background pool.
    Each qualifying signal (today: `BlockCompletedEvent`; future async
    sources plug in here) spawns an independent `asyncio.Task`. No
    serialization, no debounce — N can run in parallel. Calls the tool
    loop with `lane="background"` and the full `_MAX_TOOL_TURNS=6`. The
    `speak` tool is NOT in this lane's set; instead, on completion a
    one-line summary is appended to `persona/teacher/notices.py` so
    Lane A can surface it ("by the way, your paper is ready") on its
    next turn.

Drops:
  - Self-voice loop. `VoiceEvent.utterance.source == "teacher"` events
    are dropped at classify time (the persona just spoke; no need to
    react to itself).
  - Ambient `BlockChangeEvent`s. The reflect prompt already embeds the
    full canvas state under `=== CURRENTLY ON CANVAS ===`, so a
    separate change-bucket reflect adds noise without information.

Output of every turn:
  - tool calls take effect via the existing tool executors.
  - any text the teacher emits is sent to the debug channel as a
    `teacher-thinking` SSE event (`trigger="lane-a"` or `"lane-b"`)
    for the user's debug panel; it does NOT stream into a chat panel.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import UUID

from infra.perception import (
    BlockChangeEvent,
    BlockCompletedEvent,
    ScreenSegmentEvent,
    ScreenStoppedEvent,
    UserSpeechEvent,
    VoiceEvent,
    subscribe,
)


# This persona's name. UserSpeechEvents whose `target_persona` doesn't
# match are dropped at classify-time so they never burn the teacher's
# budget. Future personas register their own orchestrator with their
# own name.
_PERSONA_NAME = "teacher"


# ---- runtime constants ---------------------------------------------------

# Lane A: how long to wait after the latest user_speech before firing the
# turn. Lets follow-on phrases coalesce ("hello … hello … okay") into one
# fire instead of three back-to-back turns.
LANE_A_DEBOUNCE_S = 0.5

# Lane A: token cap on the user-facing reply. Single iteration of the
# tool loop, so the budget translates almost directly into output length —
# but the output now has to carry tool-call JSON too. A `mount_template`
# with a paragraph of markdown for `params.content` plus a `speak` plus
# a sentence of reasoning can easily eat 800-1200 tokens; if we cap below
# that the LLM truncates mid-tool-args and the args fall back to the
# unparseable `_raw_arguments` shape (the teacher then mounts an empty
# block or no block at all). 1500 is roomy enough for a 2-3 paragraph
# intro; longer expansions belong on a follow-up turn or in Lane B.
LANE_A_MAX_TOKENS = 1500

# Lane B: token cap on background work. Larger because the tool loop
# may chain multiple structural calls (mount → layout → push_content).
LANE_B_MAX_TOKENS = 1500


# ---- per-user trigger state --------------------------------------------


@dataclass
class _LaneAState:
    """Per-user state for the user-facing lane.

    Lane B has no per-user state — each event spawns its own task and
    the only constraint is the upstream LLM rate limit.
    """
    queue: List[Any] = field(default_factory=list)        # PerceptionEventSummary
    task: Optional[asyncio.Task] = None                   # running conversation
    debounce: Optional[asyncio.Task] = None                # pending fire timer
    # Events handed to the currently-running task. On preemption we
    # restore these to the queue so the new turn sees a merged list
    # (old transcript + new utterance), per the user's requirement that
    # "contact the old transcribe with the latest one."
    in_flight: List[Any] = field(default_factory=list)


_lane_a: Dict[str, _LaneAState] = {}


def _state_for(user_id: UUID) -> _LaneAState:
    key = str(user_id)
    s = _lane_a.get(key)
    if s is None:
        s = _LaneAState()
        _lane_a[key] = s
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


# ---- classify ------------------------------------------------------------


_AnyEvent = Union[
    BlockChangeEvent,
    BlockCompletedEvent,
    VoiceEvent,
    UserSpeechEvent,
    ScreenSegmentEvent,
    ScreenStoppedEvent,
]


def _classify(event: _AnyEvent) -> Optional[str]:
    """Map an event to its lane, or None to drop it.

    Returns "lane_a" | "lane_b" | None.
    """
    # Lane B: structural / background work.
    if isinstance(event, BlockCompletedEvent):
        return "lane_b"

    # Drop ambient block-state changes — they're already covered by the
    # `=== CURRENTLY ON CANVAS ===` snapshot in every reflect prompt.
    if isinstance(event, BlockChangeEvent):
        return None

    # Drop the persona's own speech — it just played a TTS utterance;
    # waking another reflect turn from that would be a self-feedback
    # loop. External voice events (anything not flagged "teacher")
    # route to Lane A.
    if isinstance(event, VoiceEvent):
        if getattr(event.utterance, "source", "external") == "teacher":
            return None
        return "lane_a"

    # Lane A: user spoke, addressed at this persona.
    if isinstance(event, UserSpeechEvent):
        if event.target_persona != _PERSONA_NAME:
            return None
        return "lane_a"

    # Screen-share: ambient + selective wake. Every segment is added to
    # perception by the cache; we wake Lane A only on speech segments or
    # the first vision segment after a real change (is_scene_cut). Static
    # screens (typing in a textbox) don't interrupt.
    if isinstance(event, ScreenSegmentEvent):
        if event.target_persona != _PERSONA_NAME:
            return None
        seg = event.segment
        if seg.kind == "speech" or seg.is_scene_cut:
            return "lane_a"
        return None

    # Session-stop is a perception state flip, not a wake-worthy event on
    # its own. Persona will see `screen.online = False` next time it reads.
    if isinstance(event, ScreenStoppedEvent):
        return None

    return None


def _summarise_event(event: _AnyEvent, lane: str) -> Any:
    """Convert a cache event to a `PerceptionEventSummary` for the
    reflect prompt. Imported lazily to avoid a startup cycle."""
    from persona.teacher.prompts.reflect import PerceptionEventSummary

    if isinstance(event, UserSpeechEvent):
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

    if isinstance(event, VoiceEvent):
        return PerceptionEventSummary(
            event_type="voice",
            block_id=None,
            state_kind=None,
            content=event.utterance.text,
            extra=None,
        )

    if isinstance(event, ScreenSegmentEvent):
        seg = event.segment
        extra: Dict[str, Any] = {
            "session_id": event.session_id,
            "kind": seg.kind,
            "wall_time_ms": seg.wall_time_ms,
        }
        if event.source_name:
            extra["source_name"] = event.source_name
        return PerceptionEventSummary(
            event_type=f"screen_{seg.kind}",
            block_id=None,
            state_kind=None,
            content=seg.content,
            extra=extra,
        )

    # BlockCompletedEvent (BlockChangeEvent is dropped at classify).
    state = event.state
    return PerceptionEventSummary(
        event_type="completed",
        block_id=event.block_id,
        state_kind=state.kind if state else None,
        content=(state.content if state else None),
        extra=(state.extra if state else None),
    )


# ---- entry point --------------------------------------------------------


async def _on_perception_event(event: _AnyEvent) -> None:
    """Cache-listener entry point. Routes by lane."""
    lane = _classify(event)
    if lane is None:
        return

    user_id = event.user_id
    summary = _summarise_event(event, lane)

    if lane == "lane_a":
        _lane_a_handle(user_id, summary)
    else:
        _lane_b_handle(user_id, summary)


# ---- Lane A — user-facing -----------------------------------------------


def _lane_a_handle(user_id: UUID, summary: Any) -> None:
    """Append to queue, preempt any running turn, (re)arm debounce."""
    state = _state_for(user_id)
    state.queue.append(summary)

    print(
        f"[teacher.triggers] lane_a queued: user={user_id} "
        f"running={state.task is not None and not state.task.done()} "
        f"qsize={len(state.queue)}",
        flush=True,
    )

    # If a turn is currently running, cancel it. The new event makes any
    # in-flight reply stale; we'll merge and re-fire. Restore the
    # in-flight events to the head of the queue so the next fire sees a
    # merged list (old transcript + new utterance).
    if state.task is not None and not state.task.done():
        print(
            f"[teacher.triggers] lane_a preempting running turn for user={user_id}",
            flush=True,
        )
        if state.in_flight:
            state.queue = list(state.in_flight) + state.queue
            state.in_flight = []
        state.task.cancel()

    # (Re)schedule the debounced fire. Cancel any previous debounce so
    # the latest event resets the 500 ms clock.
    if state.debounce is not None and not state.debounce.done():
        state.debounce.cancel()

    loop = asyncio.get_event_loop()
    state.debounce = loop.create_task(_lane_a_fire_after(user_id, LANE_A_DEBOUNCE_S))


async def _lane_a_fire_after(user_id: UUID, delay_s: float) -> None:
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    _lane_a_fire(user_id)


def _lane_a_fire(user_id: UUID) -> None:
    """Drain the queue (with content dedupe) and spawn a Lane A task."""
    state = _state_for(user_id)

    if state.task is not None and not state.task.done():
        # A turn is still running (cancellation may not have completed).
        # The done-callback below will arm a trailing fire.
        return

    events = list(state.queue)
    state.queue.clear()
    if not events:
        return

    # Dedupe identical-content events ("hello hello hello hello") to
    # keep the LLM focused on the latest unique phrase.
    if len(events) > 1:
        seen: Dict[str, int] = {}
        for i, e in enumerate(events):
            key = (e.content or "").strip().lower()
            if key:
                seen[key] = i
        events = [events[i] for i in sorted(seen.values())]

    loop = asyncio.get_event_loop()
    state.in_flight = list(events)
    state.task = loop.create_task(_execute_conversation(user_id, events))
    state.task.add_done_callback(lambda _t: _on_lane_a_done(user_id))


def _on_lane_a_done(user_id: UUID) -> None:
    """Done-callback for the Lane A task. Fires a trailing turn if more
    events arrived during the prior turn."""
    state = _state_for(user_id)
    state.task = None
    state.in_flight = []
    if state.queue:
        # Trailing fire — events accumulated. No debounce; fire now.
        _lane_a_fire(user_id)


# ---- Lane B — background pool -------------------------------------------


def _lane_b_handle(user_id: UUID, summary: Any) -> None:
    """Spawn a Lane B task immediately. No serialization, no per-user
    cap; multiple background tasks can run in parallel for the same
    user. Each task scopes to one event."""
    print(
        f"[teacher.triggers] lane_b firing: user={user_id} "
        f"event_type={summary.event_type} block_id={summary.block_id}",
        flush=True,
    )
    loop = asyncio.get_event_loop()
    loop.create_task(_execute_background(user_id, summary))


# ---- turn execution -----------------------------------------------------


async def _execute_conversation(user_id: UUID, events: List[Any]) -> None:
    """Run one Lane A turn — the user-facing reply.

    Cancellable: if `task.cancel()` is called (preempted by a newer
    user_speech), `CancelledError` propagates from the LLM stream. We
    swallow it, emit a phase=end SSE indicating preemption, and let the
    new debounced fire run with the merged queue.
    """
    # Late imports to avoid module-import cycles at startup.
    from persona.teacher.contexts.reflect import assemble as assemble_reflect
    from persona.teacher.tools.manifest import build_tools
    from persona.teacher.tools.loop import run as run_teacher_tool_loop
    from services.persona.routers.dynamic import enqueue_for_user
    from infra.contracts.ui import TeacherThinking

    summary = _format_events_summary(events)

    await enqueue_for_user(user_id, TeacherThinking(
        phase="start",
        trigger="lane-a",
        summary=summary,
    ))

    text_chunks: List[str] = []
    tool_calls_seen: List[Dict[str, Any]] = []
    error_text: Optional[str] = None
    preempted = False

    try:
        ctx = await assemble_reflect(user_id, events)
        async for evt in run_teacher_tool_loop(
            static_system=ctx.parts.static_system,
            static_user_passage=ctx.parts.static_user_passage,
            dynamic_user=ctx.parts.dynamic_user,
            prior_messages=ctx.prior_messages,
            tools=build_tools(user_id, lane="user_facing"),
            max_tokens=LANE_A_MAX_TOKENS,
            # 2, not 1. The loop's hard-cap at `tools/loop.py:189` breaks
            # BEFORE executing tools on the cap-hit turn — so with cap=1,
            # if turn 0 emits a truncated tool call (DeepSeek frequently
            # does this for `mount_template(params={content:...})` with
            # markdown prose) and turn 1 retries with valid args, those
            # retry args get silently dropped. Allowing one extra round
            # lets the recovery round actually execute. Cost: at most
            # one extra LLM call in the unhappy path; the happy path
            # still finishes in one round.
            max_iterations=2,
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
    except asyncio.CancelledError:
        preempted = True
        # Don't re-raise here — we want to emit the phase=end SSE first.
        # The done-callback will fire any trailing turn.
        print(
            f"[teacher.triggers] lane_a preempted for user={user_id}",
            flush=True,
        )
    except Exception as e:
        import traceback
        print(f"[teacher.triggers] lane_a error: {e}", flush=True)
        traceback.print_exc()
        error_text = f"{type(e).__name__}: {e}"
    finally:
        final_text = "".join(text_chunks).strip()
        if preempted:
            final_text = "(preempted by newer user_speech)"
        elif error_text:
            final_text = (final_text + "\n\n[error] " + error_text).strip()
        elif not final_text and not tool_calls_seen:
            final_text = "(silent — no response chosen)"
        await enqueue_for_user(user_id, TeacherThinking(
            phase="end",
            trigger="lane-a",
            summary=summary,
            text=final_text[:500],
            tool_calls=tool_calls_seen,
        ))


async def _execute_background(user_id: UUID, summary: Any) -> None:
    """Run one Lane B task — structural / background work for one event.

    Appends a one-line notice on completion (LLM-emitted text trimmed,
    or synthesized from tool calls if the model emitted no text). Lane A
    will pick it up next turn via `notices.drain(user_id)`.
    """
    # Late imports to avoid module-import cycles at startup.
    from persona.teacher.contexts.reflect import assemble as assemble_reflect
    from persona.teacher.tools.manifest import build_tools
    from persona.teacher.tools.loop import run as run_teacher_tool_loop
    from persona.teacher import notices as teacher_notices
    from services.persona.routers.dynamic import enqueue_for_user
    from infra.contracts.ui import TeacherThinking

    summary_line = _format_events_summary([summary])

    await enqueue_for_user(user_id, TeacherThinking(
        phase="start",
        trigger="lane-b",
        summary=summary_line,
    ))

    text_chunks: List[str] = []
    tool_calls_seen: List[Dict[str, Any]] = []
    error_text: Optional[str] = None

    try:
        ctx = await assemble_reflect(user_id, [summary])
        async for evt in run_teacher_tool_loop(
            static_system=ctx.parts.static_system,
            static_user_passage=ctx.parts.static_user_passage,
            dynamic_user=ctx.parts.dynamic_user,
            prior_messages=ctx.prior_messages,
            tools=build_tools(user_id, lane="background"),
            max_tokens=LANE_B_MAX_TOKENS,
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
        print(f"[teacher.triggers] lane_b error: {e}", flush=True)
        traceback.print_exc()
        error_text = f"{type(e).__name__}: {e}"
    finally:
        final_text = "".join(text_chunks).strip()
        # Build a one-line notice for Lane A to potentially surface.
        # Prefer the LLM's own text; fall back to a tool-call digest.
        notice = ""
        if final_text:
            # First non-empty line is enough for a notice — keeps Lane
            # A's prompt cheap.
            notice = final_text.split("\n", 1)[0].strip()
        elif tool_calls_seen:
            names = [tc.get("name") for tc in tool_calls_seen if tc.get("name")]
            if names:
                notice = "background: " + ", ".join(names)
        if notice:
            try:
                teacher_notices.append(user_id, notice)
            except Exception as e:
                print(f"[teacher.triggers] notice append error: {e}", flush=True)

        if error_text:
            final_text = (final_text + "\n\n[error] " + error_text).strip()
        elif not final_text and not tool_calls_seen:
            final_text = "(silent — no action chosen)"
        await enqueue_for_user(user_id, TeacherThinking(
            phase="end",
            trigger="lane-b",
            summary=summary_line,
            text=final_text[:500],
            tool_calls=tool_calls_seen,
        ))


# ---- helpers ------------------------------------------------------------


def _format_events_summary(events: List[Any]) -> str:
    """Compact one-line-per-event description for the debug-panel summary."""
    lines = []
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
        lines.append(" ".join(bits))
    return "\n".join(lines)


# ---- test hooks ---------------------------------------------------------


def _reset_for_tests() -> None:
    """Clear all per-user state. Used by tests for isolation."""
    for state in _lane_a.values():
        if state.task is not None and not state.task.done():
            state.task.cancel()
        if state.debounce is not None and not state.debounce.done():
            state.debounce.cancel()
    _lane_a.clear()
