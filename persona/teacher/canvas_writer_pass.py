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
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Set
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
_TRACE_MAX_ENTRIES = 40
_TRACE_TEXT_MAX = 2000


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
    when that child was actually called. `trace` is everything else. `failed_because` is set
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
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit} chars)"


class _Trace:
    """Ordered, bounded record of everything that is not a tool call."""

    def __init__(self):
        self.entries: List[dict] = []
        self._text: List[str] = []

    def text_chunk(self, chunk: str) -> None:
        if chunk:
            self._text.append(chunk)

    def flush_text(self) -> None:
        joined = "".join(self._text).strip()
        self._text.clear()
        if joined:
            self._add({"kind": "text", "text": _truncate(joined)})

    def add(self, entry: dict) -> None:
        self.flush_text()
        self._add(entry)

    def _add(self, entry: dict) -> None:
        if len(self.entries) < _TRACE_MAX_ENTRIES:
            self.entries.append(entry)
        elif self.entries[-1].get("kind") != "truncated":
            self.entries.append({"kind": "truncated",
                                 "note": f"trace capped at {_TRACE_MAX_ENTRIES} entries"})


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
                name = evt.get("name")
                args = evt.get("arguments") or {}
                _record_call(out, name, args)
                trace.add({"kind": "call", "name": name})
            elif kind == "delta":
                trace.text_chunk(evt.get("text") or "")
            elif kind == "done":
                # The model's own account of the turn. This is the sentence that would have
                # made the original month-long investigation a five-minute one.
                trace.text_chunk(evt.get("text") or "")
                trace.add({"kind": "done",
                           "stop_reason": evt.get("stop_reason"),
                           "tool_rounds": evt.get("tool_rounds"),
                           "deadline_hit": evt.get("deadline_hit")})
            elif kind == "tool_result":
                trace.add({"kind": "tool_result", "name": evt.get("name")})
    except Exception as e:
        out.failed_because = f"crashed:{type(e).__name__}: {e}"[:500]
        trace.add({"kind": "error", "error": out.failed_because})
    trace.flush_text()
    trace.add({"kind": "timing", "wall_ms": round((time.perf_counter() - t0) * 1000, 2)})
    out.trace = trace.entries
    return out


__all__ = [
    "MAX_TOKENS", "PROFILE", "TERMINAL_TOOLS", "WRITER_LANE",
    "WriterContractError", "WriterInputs", "WriterPass",
    "run_writer_pass", "writer_tools",
]
