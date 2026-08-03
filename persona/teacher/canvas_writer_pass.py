"""The canvas-writer decision point — ONE entry point, called by production and by eval.

WHY THIS MODULE EXISTS. `services/tuning/scorer.py::_replay` used to be a hand-copy of the
loop in `writer.py`. Every mechanical parameter matched — same `max_tokens`, same
`max_iterations`, same `terminal_tools`, same event handling. What differed was an INPUT:
production passes a real spoken answer, the eval passed `""`. The writer's prompt ends
"Mount the note (or do nothing if the spoken answer is complete on its own)", so with nothing
to mirror it correctly did nothing, scored 0.0, and the run was recorded as a wrong guide.
That ran for a month and cost a day to find.

Two copies of one call will drift, and the drift is invisible because nothing declares what
must match. So there is one function, two callers, and exactly one legal difference between
them: **evaluation stubs the child executors** (`stub_executors`), so nothing is mounted or
persisted. Prompt construction, model, token limits, iteration caps and the tool surface are
the same object, not a copy — which is why the lane is chosen here rather than passed in.

WHAT THIS RECORDS. A tool call is not a diagnostic; it is the boundary between two tunables —
the parent's output and the child's input on the same edge. `load_guide(ids)` IS the menu's
product, which is why ground truth is compared against it. So `calls` is the result. Anything
else that happened is `trace`, and both loops used to throw all of it away (`if kind !=
"tool_call": continue`) — including the model's own account of why it did nothing, which is
usually the fastest explanation of a surprising score.

THE TRACE IS A SPECIFICATION, not a log. skillforge's reflective optimizer rewrites the menu by
reading what actually happened; it cannot read our logs, our database or our model provider, so
this list is the only evidence it gets. A reader holding ONLY the trace must be able to answer
five questions, and each is one event kind:

  1. what was the model asked?          → `prompt`, one event per role, AS SENT after templating
  2. what did it say, and why?          → `text`, and `thinking` when the provider returns any
  3. what did it call, with what args?  → `call`, with `args`
  4. what came back from each call?     → `result`, with `ok` and the returned `text`
  5. why did it stop?                   → `done` / `error`

(4) is the one hosts skip. When the writer opens a guide and then authors from it, the guide's
CONTENTS are an input to the rest of that same turn — record the call and drop the response and
the second half of the run has no visible cause.

Two rules bound what goes in. Bound it: truncate long fields, never drop events. And never
synthesise: if the provider returned no reasoning, there is no `thinking` event — an absent
event is honest, a reconstructed one reads as evidence and is not.

Storing reasoning is not showing it. The trace is offline, read by optimizers and operators;
nothing here reaches a UI (every stream consumer dispatches on the kinds it knows).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from infra.model.tools import ToolExecutor, ToolSpec
from persona.teacher.prompts import canvas_guides
from persona.teacher.prompts.canvas_writer import build as build_canvas_writer_prompt
from persona.teacher.tools.loop import run as run_teacher_tool_loop
from persona.teacher.tools.manifest import build_tools


# The loop's knobs, in one place. They were literals in two files, so a change to any of
# them silently desynced production from the thing that measures it.
WRITER_LANE = "writer"
# Notes carry a big markdown payload (sections + a mermaid/plot fence with escaped unicode),
# which balloons the tool-call JSON. The 4096 default truncated it mid-args → `_raw_arguments`
# bail → nothing mounted. 8192 gives the authoring call room to complete.
MAX_TOKENS = 8192
# Terminal-on-author: mount_template/edit_note STOP the loop the instant they execute, so the
# writer makes exactly ONE decisive authoring call — re-firing a second edit_note produced
# duplicate appends and highlight spam (observed as edit_ops=['append','append'] in prod).
# `load_guide` is non-terminal: it lets the writer pull one modality's fence syntax first,
# counting toward MAX_GUIDE_DEPTH, then author.
TERMINAL_TOOLS = {"mount_template", "edit_note"}
PROFILE = "voice"

# Trace is evidence, not a log: bounded so a recorded run stays readable and storable.
# skillforge stores the list verbatim and enforces no limit, so the bound is ours to keep.
# ~8 KB per text field, a couple hundred events. The old 2000 predates `prompt` events and
# would have cut this writer's 15 KB system prompt to a fifth of itself.
_TRACE_MAX_ENTRIES = 200
_TRACE_TEXT_MAX = 8000


class WriterContractError(ValueError):
    """A required input is missing or empty, so running would measure a degenerate call.

    Raised rather than defaulted. An input that is required and empty is a bug, not a hard
    case: the run returns a defensible zero that is indistinguishable from a real regression,
    which is exactly how the original failure survived a month of evaluation.
    """

    def __init__(self, name: str):
        self.name = name
        super().__init__(
            f"canvas writer requires a non-empty `{name}` — its job is to mirror a spoken "
            f"answer onto the canvas, so with nothing to mirror it correctly does nothing "
            f"and any score for that run measures the harness, not the candidate.")


@dataclass(frozen=True)
class WriterInputs:
    """What a case must supply for this decision to run at all.

    The first two are REQUIRED (see `WriterContractError`). The rest are context the writer
    reads if it has it; production gathers them from the live canvas and the note store, and
    a scenario may supply them to exercise the same branches.
    """
    question: str
    voice_transcript: str
    canvas_state: Any = None
    existing_notes: Optional[Dict[str, str]] = None
    related_notes: Optional[List[dict]] = None
    # The variant itself. Injected, never resolved here — production resolves it from
    # skillforge, evaluation is handed the candidate. That asymmetry is the caller's.
    menu_config: Optional[dict] = None


@dataclass
class WriterPass:
    """What the pass produced.

    `calls` is the OUTPUT — what was handed to each child, keyed by child name, present only
    when that child was actually called. `trace` is the NARRATIVE — ordering, arguments as
    sent, and what came back. Both are sent to skillforge and they are not redundant: an
    evaluator scores the first, a reflective emitter reads the second. `failed_because` is set
    only for a failure this function itself can name; a caller's own verdict (wrong guide,
    off-menu) is the caller's to name.
    """
    calls: Dict[str, dict] = field(default_factory=dict)
    trace: List[dict] = field(default_factory=list)
    failed_because: Optional[str] = None
    # Views over `calls`, computed once here so neither caller reconstructs the result from
    # the tool calls a second time and drifts in how it does so.
    selected_guides: Set[str] = field(default_factory=set)
    authored_parts: List[str] = field(default_factory=list)
    mount_fired: bool = False
    edit_ops: List[str] = field(default_factory=list)


def writer_tools(user_id: UUID,
                 stub_executors: Optional[Dict[str, ToolExecutor]] = None) -> List[ToolSpec]:
    """The writer-lane toolset, optionally with child executors swapped for recorders.

    THE ONE LEGAL DIFFERENCE between production and evaluation. The LLM-facing surface —
    names, descriptions, schemas, including any served `tool.*` description tuning — stays
    byte-identical; only the side effects go. Stubbing is expressed as a swap on the real
    toolset rather than a separately-assembled one, so an eval cannot quietly run a
    different lane.
    """
    tools = build_tools(user_id, lane=WRITER_LANE)
    if not stub_executors:
        return tools
    return [replace(t, executor=stub_executors[t.name]) if t.name in stub_executors else t
            for t in tools]


def _truncate(text: str, limit: int = _TRACE_TEXT_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[truncated {len(text) - limit} chars]"


def _truncate_middle(text: str, limit: int = _TRACE_TEXT_MAX) -> str:
    """Bound a prompt while keeping both ends.

    Head-first truncation is wrong for exactly this prompt: the writer's system
    message is ~15 KB of canvas skills with the TUNED MENU appended last, so
    cutting the tail throws away the artifact skillforge is optimizing while
    faithfully preserving four skills it cannot change. Keep both ends and say
    in the middle how much is missing.
    """
    if len(text) <= limit:
        return text
    head = (limit * 2) // 3
    tail = limit - head
    return (text[:head]
            + f"\n…[truncated {len(text) - limit} chars]\n"
            + text[len(text) - tail:])


def _bounded_args(value: Any, depth: int = 0) -> Any:
    """Tool arguments with every string bounded — the note markdown is the big one.

    Arguments go in as sent (recovered exactly as the executor sees them); only
    their length is touched, and a truncated string says so in-band.
    """
    if isinstance(value, str):
        return _truncate(value)
    if depth >= 4:
        return value
    if isinstance(value, dict):
        return {k: _bounded_args(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_bounded_args(v, depth + 1) for v in value]
    return value


class _Trace:
    """Ordered, bounded record of a run — the five evidence kinds, in order.

    Streamed text arrives as many chunks and one `kind`; `chunk()` coalesces a
    contiguous run of them into a single event and flushes automatically when
    the kind changes or a discrete event (a call, a result) interleaves. That
    keeps `thinking` and `text` from being spliced into each other while
    preserving the order they actually happened in.
    """

    def __init__(self):
        self.entries: List[dict] = []
        self._pending_kind: Optional[str] = None
        self._pending: List[str] = []

    def chunk(self, kind: str, text: str) -> None:
        if not text:
            return
        if self._pending_kind is not None and self._pending_kind != kind:
            self.flush()
        self._pending_kind = kind
        self._pending.append(text)

    def flush(self) -> None:
        joined = "".join(self._pending).strip()
        kind = self._pending_kind
        self._pending.clear()
        self._pending_kind = None
        if joined and kind:
            self._add({"kind": kind, "text": _truncate(joined)})

    def add(self, entry: dict) -> None:
        self.flush()
        self._add(entry)

    def _add(self, entry: dict) -> None:
        if len(self.entries) < _TRACE_MAX_ENTRIES:
            self.entries.append(entry)
        elif self.entries[-1].get("kind") != "truncated":
            self.entries.append({"kind": "truncated",
                                 "note": f"trace capped at {_TRACE_MAX_ENTRIES} entries"})


def recovered_args(args: dict) -> Tuple[dict, bool]:
    """Args as the executor will see them, plus whether they were truly truncated.

    `_raw_arguments` is the provider's fallback shape when the tool-arg stream was not parsed
    into structured fields, and DeepSeek's tool channel emits a COMPLETE valid JSON object in
    it even on SUCCESSFUL calls. `mount_template`'s executor therefore recovers it and only
    bails when it does not parse.

    Recording the un-recovered event is how the authored markdown went missing from the record
    on most real turns: the note mounted fine because the executor recovered, while the outcome
    signal and every replay's `calls` read an empty `params` and concluded nothing was authored.
    A recorder that sees less than the executor does is not recording the call.
    """
    raw = args.get("_raw_arguments")
    if raw is None:
        return args, False
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return args, True
        if isinstance(parsed, dict):
            return parsed, False
    return args, True


def _record_call(out: WriterPass, name: str, args: dict) -> None:
    """Fold one tool call into `calls` and the views over it.

    Argument paths (`params.markdown`) match what the tunable declares to skillforge, so the
    recorded call and the declaration name the same thing.
    """
    if name == "load_guide":
        ids = args.get("ids") or []
        if isinstance(ids, list):
            picked = [str(i).strip() for i in ids]
            out.selected_guides.update(picked)
            out.calls.setdefault("load_guide", {}).setdefault("ids", []).extend(picked)
    elif name == "mount_template":
        out.mount_fired = True
        md = (args.get("params") or {}).get("markdown")
        slot = out.calls.setdefault("mount_template", {}).setdefault("params.markdown", [])
        if isinstance(md, str):
            out.authored_parts.append(md)
            slot.append(md)
    elif name == "edit_note":
        slot = out.calls.setdefault("edit_note", {}).setdefault("ops", [])
        for op in args.get("ops") or []:
            if not isinstance(op, dict):
                continue
            slot.append(op)
            if op.get("op"):
                out.edit_ops.append(op["op"])
            if isinstance(op.get("md"), str):
                out.authored_parts.append(op["md"])


async def run_writer_pass(
    *,
    inputs: WriterInputs,
    user_id: UUID,
    purpose: str,
    stub_executors: Optional[Dict[str, ToolExecutor]] = None,
) -> WriterPass:
    """Build the writer prompt, run the tool loop, record what it handed to its children.

    Raises `WriterContractError` when a required input is empty. Every other failure is
    reported on the returned `WriterPass` — the loop's own exceptions land in
    `failed_because` and `trace` rather than propagating, because the production caller is a
    detached task whose originating stream is already closed.
    """
    if not (inputs.question or "").strip():
        raise WriterContractError("question")
    if not (inputs.voice_transcript or "").strip():
        raise WriterContractError("voice_transcript")

    parts = build_canvas_writer_prompt(
        question=inputs.question,
        voice_transcript=inputs.voice_transcript,
        canvas_state=inputs.canvas_state,
        existing_notes=inputs.existing_notes,
        related_notes=inputs.related_notes,
        menu_config=inputs.menu_config,
    )

    out = WriterPass()
    trace = _Trace()

    # Q1 — what was the model asked. Every message AS SENT, after templating, one
    # event per role, taken from the same `parts` handed to the loop below rather
    # than rebuilt (a re-render is a plausible prompt, not the one that ran).
    trace.add({"kind": "prompt", "role": "system",
               "text": _truncate_middle(parts.static_system or "")})
    for message in (parts.static_user_passage, parts.dynamic_user):
        if (message or "").strip():
            trace.add({"kind": "prompt", "role": "user",
                       "text": _truncate_middle(message)})

    # The loop's final `done` carries the whole answer text accumulated across
    # turns, which the deltas already delivered. Record it only when no delta
    # ever did — otherwise every streamed turn lands in the trace twice.
    saw_streamed_text = False
    t0 = time.perf_counter()
    try:
        async for evt in run_teacher_tool_loop(
            static_system=parts.static_system,
            static_user_passage=parts.static_user_passage,
            dynamic_user=parts.dynamic_user,
            prior_messages=None,
            tools=writer_tools(user_id, stub_executors),
            purpose=purpose,
            user_id=user_id,
            max_tokens=MAX_TOKENS,
            max_iterations=canvas_guides.MAX_GUIDE_DEPTH + 1,
            terminal_tools=TERMINAL_TOOLS,
            profile=PROFILE,
        ):
            kind = evt.get("kind")
            if kind == "tool_call":
                # Q3 — what it called, WITH THE ARGUMENTS AS SENT. A name alone says a
                # run went wrong and nothing about why; `load_guide` with no `ids` cannot
                # be told apart from `load_guide(['mermaid'])` on a plotting request.
                name = evt.get("name")
                args, truncated = recovered_args(evt.get("arguments") or {})
                _record_call(out, name, args)
                entry = {"kind": "call", "name": name, "args": _bounded_args(args)}
                # A truncated authoring call is the one failure the writer can recover from
                # by retrying, so it belongs in the record rather than looking like a call
                # that simply carried no content.
                if truncated:
                    entry["truncated"] = True
                trace.add(entry)
            elif kind == "tool_result":
                # Q4 — what came back. The guide body `load_guide` returns is an INPUT to
                # the rest of this same turn: the note authored two events later was
                # written from it, so without this the second half of the run has no
                # visible cause. This is the gap hosts most often leave.
                trace.add({"kind": "result",
                           "name": evt.get("name"),
                           "ok": bool(evt.get("ok")),
                           "text": _truncate(str(evt.get("text") or ""))})
            elif kind == "delta":
                chunk = evt.get("text") or ""
                saw_streamed_text = saw_streamed_text or bool(chunk.strip())
                trace.chunk("text", chunk)
            elif kind == "thinking":
                # Q2's second half — the model's reasoning, verbatim, ONLY when the
                # provider actually returned some. No provider reasoning, no event: a
                # rationale reconstructed after the fact reads as evidence and is not one.
                trace.chunk("thinking", evt.get("text") or "")
            elif kind == "done":
                # The model's own account of the turn. This is the sentence that would have
                # made the original month-long investigation a five-minute one.
                if not saw_streamed_text:
                    trace.chunk("text", evt.get("text") or "")
                trace.add({"kind": "done",
                           "stop_reason": evt.get("stop_reason"),
                           "tool_rounds": evt.get("tool_rounds"),
                           "deadline_hit": evt.get("deadline_hit")})
    except Exception as e:
        out.failed_because = f"crashed:{type(e).__name__}: {e}"[:500]
        trace.add({"kind": "error", "text": out.failed_because})
    trace.flush()
    trace.add({"kind": "timing", "wall_ms": round((time.perf_counter() - t0) * 1000, 2)})
    out.trace = trace.entries
    return out


__all__ = [
    "MAX_TOKENS", "PROFILE", "TERMINAL_TOOLS", "WRITER_LANE",
    "WriterContractError", "WriterInputs", "WriterPass",
    "recovered_args", "run_writer_pass", "writer_tools",
]
