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

# Lane R (research): the long-horizon investigator. Spawned by
# `start_research` from Lane A or /api/ask. Has the full browser
# toolkit + planning scaffold (research_plan / research_note).
# Iterations are generous because real investigation chains many
# tool calls; tokens are larger because the LLM has to reason over
# fuller browser-page excerpts; tool-result truncation is loosened
# because 2000 chars throws away most of a real web page.
LANE_R_MAX_TOKENS = 4096
LANE_R_MAX_ITERATIONS = 25
LANE_R_WALL_CLOCK_S = 120.0
LANE_R_TOOL_RESULT_CHARS = 6000
# Trigger the deadline-warning at this many seconds before the hard cap.
# Wider window = the model gets more rounds after seeing the warning to
# actually land its synthesis. Empirically ~30 s is needed for a tool
# loop to converge on a final speak; tighter and the model runs out
# mid-tool-call.
LANE_R_DEADLINE_GRACE_S = 30.0


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

    # Screen-share: cache-only, never wakes a lane. The segment is
    # already written to the perception cache by `record_screen_segment`
    # independent of this classifier — Lane A reads the latest
    # `ScreenPerception` from cache the next time user voice legitimately
    # wakes it. Routing screen events to Lane A here caused
    # `_lane_a_handle` to cancel the user's in-flight reply every time
    # the screen changed (scene-cut mid-reply ⇒ preemption ⇒ debounced
    # restart). Lane B is also wrong because each event would spawn its
    # own tool loop. If a proactive "watcher" is wanted later, design it
    # as a separate timer-based reader, not a per-event wake.
    if isinstance(event, ScreenSegmentEvent):
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


def _build_fallback_synthesis(state: Any, text_chunks: List[str], deadline_hit: bool) -> str:
    """Deterministic synthesis from recorded notes — used as a safety net
    when the research loop ends without the model calling `speak`.

    Strategy: prefer the agent's own free-text if it's substantive (the
    model often emits useful prose alongside tool calls). Otherwise stitch
    together the highest-information notes verbatim. Always grounded —
    we never invent content the agent didn't observe.
    """
    free_text = "".join(text_chunks).strip()
    note_findings = []
    if state is not None:
        for s in (state.steps or []):
            if s.note and s.status in ("done", "doing"):
                note_findings.append(s.note.strip())

    if free_text and len(free_text) >= 80:
        # Trust the model's own prose — it's already grounded in tool
        # results. Trim long emissions so we don't dump a transcript.
        prefix = "Based on what I found: " if not free_text.lower().startswith("based") else ""
        return (prefix + free_text).strip()[:1000]

    if note_findings:
        bullets = "; ".join(note_findings[:5])
        prefix = "Based on what I observed"
        if deadline_hit:
            prefix += " (research time ran out before I could finalize)"
        return f"{prefix}: {bullets}".strip()[:1000]

    if deadline_hit:
        return (
            "Sorry — I ran out of time investigating before I could "
            "give you a grounded answer. Try asking again, or narrow "
            "the question."
        )
    return ""


# Reflection prompt — kept inline (only used here, ~20 lines). If a
# future persona wants its own flavor, factor into a skill file.
_PER_HOST_REFLECT_SYSTEM = (
    "You just finished researching on the site `{host}`. The user's "
    "question was `{goal}`, but THIS REFLECTION IS NOT ABOUT THE TOPIC. "
    "Below is the tool sequence you ran. Write a NAVIGATION NOTE for "
    "your future self about navigating `{host}` in general — any future "
    "research on this site (different topic, different user) will see "
    "this note prepended to its prompt and use it to skip rediscovery.\n"
    "\n"
    "Lean toward SAVING. Even modest observations help: which tools "
    "worked for this kind of page, which sections / anchors / URLs are "
    "rich, what shapes the page has (infoboxes, navboxes, citation "
    "blocks, sub-anchors like #History). The site you researched is "
    "almost certainly used again — so any structural fact you can "
    "share with a future agent is worth ≤ 5 sentences.\n"
    "\n"
    "Examples of good notes:\n"
    "  - \"Wikipedia: sub-anchor URLs (#History, #Legacy) are the fastest "
    "way to read a specific section; read_url on the bare article URL "
    "returns only the lead + first ~12 KB. Snapshot+@ref on long "
    "articles reliably surfaces the heading hierarchy.\"\n"
    "  - \"finance.google.com#Key-Stats has price + 52-week range "
    "without scrolling; news is below the chart and needs scroll.\"\n"
    "  - \"Sites with anti-bot fingerprinting (NYTimes, WSJ): use "
    "web_view, not read_url.\"\n"
    "\n"
    "Anti-examples (these are TOPIC findings, NOT navigation notes):\n"
    "  - ❌ \"Genghis Khan invaded Europe in 1241.\"\n"
    "  - ❌ \"The stock is up 1.2% today.\"\n"
    "  - ❌ Anything that quotes the article's content, not its structure.\n"
    "\n"
    "Output ONLY the note text — no preamble, no quotes around it. "
    "Return exactly `NO_NOTE` only if the run was so trivial there is "
    "literally nothing structural to share."
)


async def _reflect_per_host_skill(
    host: str, goal: str, tool_calls_seen: List[Dict[str, Any]],
) -> None:
    """Ask the LLM to produce a navigation note for `host` based on the
    tool sequence the research run just executed. Save via
    `per_host_skills.save` (which itself fires an LLM-mediated merge if
    a prior note exists for this host)."""
    from infra.model.llm import generate_cached
    from workshop.research import per_host_skills

    # Build a richer tool-sequence summary. Names alone aren't enough —
    # the model needs to see which ACTIONS fired (snapshot? evaluate?
    # text-on-@ref?) and the rough shape of args (URL hosts, action
    # verbs) to recognize navigation patterns. We omit free-form values
    # (selectors, search text, raw evaluate JS) to keep the token budget
    # tight while keeping the structural signal.
    seq_lines: List[str] = []
    for i, c in enumerate(tool_calls_seen, start=1):
        name = c.get("name") or "?"
        args = c.get("arguments") or {}
        bits: List[str] = []
        if name == "browser_set":
            action = (args.get("action") or "").strip()
            bits.append(f"action={action}")
            if args.get("url"):
                # Show only the path+anchor, not the full URL (host is
                # already known from the file scope).
                from urllib.parse import urlparse
                u = urlparse(str(args["url"]))
                bits.append(f"path={u.path}{('#' + u.fragment) if u.fragment else ''}")
            if isinstance(args.get("selector"), str):
                sel = args["selector"]
                bits.append(f"sel={'@e<n>' if sel.startswith('@e') else sel[:40]}")
        elif name == "read_url":
            if args.get("url"):
                from urllib.parse import urlparse
                u = urlparse(str(args["url"]))
                bits.append(f"path={u.path}{('#' + u.fragment) if u.fragment else ''}")
        elif name == "research_plan":
            bits.append(f"steps={len(args.get('steps') or [])}")
        elif name == "research_note":
            bits.append(f"step={args.get('step_index')}")
        elif name == "speak":
            bits.append("(final synthesis)")
        seq_lines.append(f"  {i:2}. {name}({', '.join(bits)})")
    seq_block = "\n".join(seq_lines)

    try:
        text, usage = await generate_cached(
            static_system=_PER_HOST_REFLECT_SYSTEM.format(host=host, goal=goal[:200]),
            static_user_passage="",
            dynamic_user=(
                f"Goal you just researched: {goal.strip()}\n"
                f"Site: {host}\n"
                f"Tool sequence ({len(tool_calls_seen)} calls):\n"
                f"{seq_block}\n"
            ),
            prior_messages=None,
            max_tokens=800,
            purpose="research-per-host-reflection",
        )
        print(
            f"[per_host_skills] reflection LLM usage: "
            f"in={usage.get('input_tokens')} out={usage.get('output_tokens')}",
            flush=True,
        )
    except Exception as e:
        print(f"[per_host_skills] reflection LLM failed: {e}", flush=True)
        return

    note = (text or "").strip()
    print(
        f"[per_host_skills] reflection LLM returned {len(note)} chars for {host}: "
        f"{note[:120]!r}",
        flush=True,
    )
    if not note:
        return
    await per_host_skills.save(host, note)


async def _execute_research(
    user_id: UUID, goal: str, goal_url: Optional[str] = None,
) -> None:
    """Run one Lane R turn — the long-horizon investigator.

    Spawned by `start_research` (the executor lives in `tools/manifest.py`)
    when Lane A judges a question requires multi-step investigation.
    Owns its own lifecycle: builds a research-mode context, runs the
    tool loop with the research lane / iteration / token / deadline
    overrides, and tears down the in-memory research state in the
    finally block so the next question starts clean.

    The loop ends with `speak` (per research_policy.md), which delivers
    the synthesis to the user via TTS + caption. Free-form delta text
    from the LLM is surfaced to the debug panel as TeacherThinking,
    same as Lane A/B.
    """
    # Late imports to avoid module-import cycles at startup.
    from persona.teacher.contexts.research import assemble as assemble_research
    from persona.teacher.tools.manifest import build_tools
    from persona.teacher.tools.loop import run as run_teacher_tool_loop
    from persona.teacher import research_state
    from services.persona.routers.dynamic import enqueue_for_user
    from infra.contracts.ui import TeacherThinking

    summary_line = f"research: {goal[:80]}"

    await enqueue_for_user(user_id, TeacherThinking(
        phase="start",
        trigger="lane-r",
        summary=summary_line,
    ))

    text_chunks: List[str] = []
    tool_calls_seen: List[Dict[str, Any]] = []
    # Captured from the FIRST `browser_set(action="snapshot")` result —
    # the @ref baseline for parameterizing the recipe's tool calls.
    captured_refs: List[Dict[str, Any]] = []
    speak_called = False
    error_text: Optional[str] = None
    deadline_hit = False

    try:
        ctx = await assemble_research(user_id, goal, goal_url=goal_url)
        async for evt in run_teacher_tool_loop(
            static_system=ctx.parts.static_system,
            static_user_passage=ctx.parts.static_user_passage,
            dynamic_user=ctx.parts.dynamic_user,
            prior_messages=ctx.prior_messages,
            tools=build_tools(user_id, lane="research"),
            max_tokens=LANE_R_MAX_TOKENS,
            max_iterations=LANE_R_MAX_ITERATIONS,
            wall_clock_deadline_s=LANE_R_WALL_CLOCK_S,
            deadline_grace_s=LANE_R_DEADLINE_GRACE_S,
            tool_result_max_chars=LANE_R_TOOL_RESULT_CHARS,
            purpose="research",
            user_id=user_id,
        ):
            kind = evt.get("kind")
            if kind == "delta":
                text_chunks.append(evt.get("text", ""))
            elif kind == "tool_call":
                name = evt.get("name")
                tool_calls_seen.append({
                    "name": name,
                    "arguments": evt.get("arguments") or {},
                })
                if name == "speak":
                    speak_called = True
            elif kind == "tool_result":
                # Capture refs from the FIRST snapshot only. The recipe's
                # parameterized @ref tokens are anchored against this set.
                if (
                    not captured_refs
                    and evt.get("name") == "browser_set"
                    and evt.get("action") == "snapshot"
                ):
                    result = evt.get("result") or {}
                    refs = result.get("refs") or []
                    if isinstance(refs, list):
                        captured_refs = list(refs)
            elif kind == "done":
                deadline_hit = bool(evt.get("deadline_hit"))
    except Exception as e:
        import traceback
        print(f"[teacher.triggers] lane_r error: {e}", flush=True)
        traceback.print_exc()
        error_text = f"{type(e).__name__}: {e}"
    finally:
        # Synthesis safety net: if the loop ended without the model
        # calling `speak` (deadline ran out, model went into a tool-call
        # loop, etc.), construct a deterministic synthesis from the
        # research_state notes + any free-text the model emitted, and
        # deliver it via the speak channel. The user gets an answer
        # grounded in what the agent actually observed, even when the
        # agent itself didn't land the synthesis.
        if not speak_called and not error_text:
            try:
                state = research_state.get(user_id)
                if state is not None and (state.steps or text_chunks):
                    synth = _build_fallback_synthesis(state, text_chunks, deadline_hit)
                    if synth:
                        from tools.speak import speak as _speak
                        try:
                            await _speak(
                                user_id=user_id,
                                text=synth,
                                channel="both",
                            )
                            tool_calls_seen.append({
                                "name": "speak",
                                "arguments": {"text": synth, "channel": "both"},
                            })
                            speak_called = True
                            print(
                                f"[teacher.triggers] lane_r fallback synthesis "
                                f"delivered ({len(synth)} chars)",
                                flush=True,
                            )
                        except Exception as e:
                            print(
                                f"[teacher.triggers] fallback speak failed: {e}",
                                flush=True,
                            )
            except Exception as e:
                print(f"[teacher.triggers] fallback synthesis error: {e}", flush=True)

        # Mark the research run finished and push the final state to the
        # ribbon so it collapses to a chip. Then drop the in-memory state
        # so a future start_research starts clean.
        try:
            state = research_state.finish(user_id)
            if state is not None:
                # Push final state via the same path the planning tools
                # use. Late import — manifest imports triggers, so we
                # can't import the helper at module load.
                from persona.teacher.tools.manifest import (
                    _push_research_state_to_canvas,
                )
                await _push_research_state_to_canvas(user_id)
        except Exception as e:
            print(f"[teacher.triggers] lane_r teardown error: {e}", flush=True)
        finally:
            research_state.clear(user_id)

        # Recipe recording: if the run actually succeeded (LLM landed a
        # synthesis OR the fallback delivered one), no error, and the
        # sequence touched at least one URL we can parameterize — persist
        # a replay recipe. Two flavors:
        #   - URL+ref recipes (when the agent called snapshot): captured_refs
        #     anchors the @e<n> tokens to (role, name) pairs for remap.
        #   - URL-only recipes (when the agent stuck to read_url + speak):
        #     `recorded_refs` is empty; the replay path skips the smoke
        #     test and just URL-substitutes. Still a big win for Wikipedia-
        #     class flows where the agent rarely needs the snapshot tool.
        # Awaited (not fire-and-forget): the embed+write is ~100 ms and
        # `asyncio.run` can cancel orphaned tasks at shutdown.
        if (
            speak_called
            and not error_text
            and len(tool_calls_seen) >= 3
        ):
            try:
                from workshop.research.recipes import (
                    record_after_success,
                    host_from_calls,
                )
                if host_from_calls(tool_calls_seen):
                    await record_after_success(
                        user_id, goal, tool_calls_seen, captured_refs,
                    )
                else:
                    print(
                        "[teacher.triggers] lane_r recipe-skip "
                        "(no URL in tool sequence to parameterize)",
                        flush=True,
                    )
            except Exception as e:
                print(f"[teacher.triggers] lane_r recipe-record error: {e}", flush=True)

        # Per-host skill reflection: after a successful research run, ask
        # the model to write a short navigation note about the SITE (not
        # the topic) so future research on the same host inherits the
        # learning. Awaited so the merge LLM call lands before the trigger
        # returns; ~3 s additional latency, off the user's critical path
        # (synthesis has already been spoken). Failures degrade silently
        # — the note is enrichment, not required.
        if (
            speak_called
            and not error_text
            and len(tool_calls_seen) >= 3
        ):
            try:
                from workshop.research.recipes import (
                    host_from_calls,
                    host_from_url as _host_from_url,
                )
                reflect_host = (
                    (_host_from_url(goal_url) if goal_url else None)
                    or host_from_calls(tool_calls_seen)
                )
                if reflect_host:
                    await _reflect_per_host_skill(
                        reflect_host, goal, tool_calls_seen,
                    )
            except Exception as e:
                print(f"[teacher.triggers] lane_r per_host reflection error: {e}", flush=True)

        final_text = "".join(text_chunks).strip()
        if error_text:
            final_text = (final_text + "\n\n[error] " + error_text).strip()
        elif deadline_hit and not final_text:
            final_text = "(research turn hit 90 s wall-clock cap)"
        elif not final_text and not tool_calls_seen:
            final_text = "(silent — no action chosen)"
        await enqueue_for_user(user_id, TeacherThinking(
            phase="end",
            trigger="lane-r",
            summary=summary_line,
            text=final_text[:500],
            tool_calls=tool_calls_seen,
        ))


async def _execute_research_from_recipe(
    user_id: UUID,
    goal: str,
    runtime_url: str,
    recipe: Any,  # workshop.research.recipe_store.ResearchRecipe
) -> None:
    """Replay a saved research recipe instead of running fresh Lane R.

    Same SSE/ribbon contract as `_execute_research`, but the body is:
      smoke_test (~2-3 s) → replay tool sequence (~3-5 s)
        → one generate_cached synthesis call (~3-5 s) → speak.

    On smoke-test failure, falls through to fresh `_execute_research`
    so the user always gets a grounded answer (worst case: 2 wasted
    tool calls).
    """
    from persona.teacher.contexts.research import assemble as assemble_research
    from persona.teacher import research_state
    from services.persona.routers.dynamic import enqueue_for_user
    from infra.contracts.ui import TeacherThinking
    from infra.model.llm import generate_cached
    from workshop.research import recipe_runner, recipe_store

    summary_line = f"research-replay: {goal[:80]}"

    await enqueue_for_user(user_id, TeacherThinking(
        phase="start",
        trigger="lane-r-replay",
        summary=summary_line,
    ))

    # Seed the progress ribbon with two synthetic steps so the user
    # sees activity. This matches the Lane R UX.
    try:
        research_state.begin(user_id, goal=goal)
        research_state.set_plan(user_id, [
            "Replaying saved procedure",
            "Writing up findings",
        ])
        from persona.teacher.tools.manifest import (
            _push_research_state_to_canvas,
        )
        await _push_research_state_to_canvas(user_id)
    except Exception as e:
        print(f"[lane-r-replay] ribbon seed failed: {e}", flush=True)

    tool_calls_seen: List[Dict[str, Any]] = []
    error_text: Optional[str] = None
    synth_text = ""

    try:
        # 1. Smoke test — load page, snapshot, remap refs by (role, name).
        ref_remap = await recipe_runner.smoke_test(user_id, recipe, runtime_url)
        if ref_remap is None:
            print(
                f"[lane-r-replay] smoke failed for recipe {recipe.id}; "
                "falling through to fresh Lane R",
                flush=True,
            )
            # Tear down the ribbon — fresh Lane R will set up its own.
            try:
                research_state.clear(user_id)
            except Exception:
                pass
            await enqueue_for_user(user_id, TeacherThinking(
                phase="end",
                trigger="lane-r-replay",
                summary=summary_line,
                text="(smoke test failed; running fresh investigation)",
                tool_calls=[],
            ))
            return await _execute_research(user_id, goal)

        # 2. Replay tool sequence — data collection only.
        async def _on_step(text: str) -> None:
            # Find the next pending step and mark it doing. Note that
            # research_state.record_note auto-advances on done; here we
            # just push a status text via the existing ribbon.
            try:
                state = research_state.get(user_id)
                if state is None:
                    return
                # Mark the first step done once we have a step happening.
                for i, s in enumerate(state.steps):
                    if s.status == "doing":
                        research_state.record_note(user_id, i, text)
                        break
                await _push_research_state_to_canvas(user_id)
            except Exception:
                pass

        collected = await recipe_runner.run_recipe(
            user_id, recipe, runtime_url, ref_remap, on_step=_on_step,
        )
        tool_calls_seen.extend([
            {"name": c["tool_name"], "arguments": c.get("args") or {}}
            for c in collected
        ])

        # 3. Synthesis — teacher's full research prompt + collected findings.
        ctx = await assemble_research(user_id, goal, goal_url=runtime_url)
        findings_block = _format_collected_for_prompt(collected)
        synth_text, _usage = await generate_cached(
            static_system=ctx.parts.static_system,
            static_user_passage="",
            dynamic_user=(
                "=== RESEARCH GOAL ===\n"
                f"{goal.strip()}\n\n"
                "=== COLLECTED FINDINGS (replayed from saved recipe) ===\n"
                f"{findings_block}\n\n"
                "Synthesize a grounded answer in your normal teacher voice. "
                "Quote concrete facts from the findings. Hedge with 'based on "
                "what's on the page' — you only know what was observed."
            ),
            prior_messages=None,
            max_tokens=1024,
            purpose="research-replay-synthesis",
            user_id=user_id,
        )
        synth_text = (synth_text or "").strip()

        # 4. Deliver via speak.
        if synth_text:
            from tools.speak import speak as _speak
            try:
                await _speak(
                    user_id=user_id,
                    text=synth_text,
                    channel="both",
                )
                tool_calls_seen.append({
                    "name": "speak",
                    "arguments": {"text": synth_text, "channel": "both"},
                })
            except Exception as e:
                print(f"[lane-r-replay] speak failed: {e}", flush=True)

        # 5. Bump recipe usage.
        try:
            await recipe_store.mark_used(user_id, recipe.id)
        except Exception as e:
            print(f"[lane-r-replay] mark_used failed: {e}", flush=True)
    except Exception as e:
        import traceback
        print(f"[teacher.triggers] lane-r-replay error: {e}", flush=True)
        traceback.print_exc()
        error_text = f"{type(e).__name__}: {e}"
    finally:
        try:
            research_state.finish(user_id)
            from persona.teacher.tools.manifest import (
                _push_research_state_to_canvas,
            )
            await _push_research_state_to_canvas(user_id)
        except Exception as e:
            print(f"[lane-r-replay] teardown error: {e}", flush=True)
        finally:
            research_state.clear(user_id)

        final_text = synth_text or "(no synthesis produced)"
        if error_text:
            final_text = (final_text + "\n\n[error] " + error_text).strip()
        await enqueue_for_user(user_id, TeacherThinking(
            phase="end",
            trigger="lane-r-replay",
            summary=summary_line,
            text=final_text[:500],
            tool_calls=tool_calls_seen,
        ))


def _format_collected_for_prompt(collected: List[Dict[str, Any]]) -> str:
    """Compact text dump of replay results for the synthesis prompt.
    One block per call. Each result is truncated to ~1.5 KB so the
    synthesis prompt stays well under the model's window."""
    blocks: List[str] = []
    for i, c in enumerate(collected, start=1):
        name = c.get("tool_name") or "?"
        args = c.get("args") or {}
        result = c.get("result")
        # Best-effort text representation of the result.
        try:
            if isinstance(result, dict):
                if "text" in result:
                    body = str(result.get("text") or "")
                elif "description" in result:
                    body = str(result.get("description") or "")
                else:
                    import json as _json
                    body = _json.dumps(result, default=str)
            else:
                body = str(result)
        except Exception:
            body = str(result)
        body = body.strip()
        if len(body) > 1500:
            body = body[:1500] + f"\n…[truncated {len(body) - 1500} chars]"
        # Compact arg preview: skip empty values + long URLs.
        arg_summary = ", ".join(
            f"{k}={str(v)[:60]}"
            for k, v in args.items()
            if v not in (None, "", False)
        )
        blocks.append(f"[{i}] {name}({arg_summary})\n{body}")
    return "\n\n".join(blocks)


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
