"""One entry point, two callers — the invariant that makes the original bug impossible.

`services/tuning/scorer.py::_replay` was a hand-copy of the loop in `persona/teacher/writer.py`.
Every mechanical parameter matched. What differed was an INPUT: production passed a real spoken
answer, the eval passed `""`, and since the writer exists to mirror a spoken answer it correctly
did nothing, scored 0.0, and was recorded as a wrong guide for a month.

These tests hold the two halves of the fix: the copy cannot come back (there is one function and
the loop is invoked from one place), and a degenerate input is a loud refusal rather than a
defensible number.
"""
import asyncio
import pathlib
from uuid import UUID

import pytest

from persona.teacher import canvas_writer_pass, writer
from persona.teacher.canvas_writer_pass import (
    WriterContractError, WriterInputs, run_writer_pass,
)
from services.tuning import scorer

_USER = UUID("00000000-0000-4000-8000-0000e5a10002")
_ROOT = pathlib.Path(__file__).resolve().parents[2]


class _FakeLoop:
    """Stands in for the teacher tool loop; records the knobs it was handed."""

    def __init__(self, events=()):
        self.kwargs = None
        self._events = list(events)

    def __call__(self, **kwargs):
        self.kwargs = kwargs

        async def _gen():
            for e in self._events:
                yield e
        return _gen()


def _inputs(**over):
    base = dict(question="plot y = x^2 over [-3, 3]",
                voice_transcript="A parabola bottoms out at the origin.")
    base.update(over)
    return WriterInputs(**base)


def _run(inputs=None, **kw):
    return asyncio.run(run_writer_pass(
        inputs=inputs or _inputs(), user_id=_USER, purpose="test", **kw))


# ---- the copy cannot come back ---------------------------------------------------------

def test_production_and_eval_call_the_same_function_object():
    """Not "two functions that agree" — one function, two callers. Convergence by convention
    is what drifted; identity cannot."""
    assert writer.run_writer_pass is canvas_writer_pass.run_writer_pass
    assert scorer.run_writer_pass is canvas_writer_pass.run_writer_pass


def test_neither_caller_holds_a_second_copy_of_the_loop():
    """The knobs — max_tokens, terminal_tools, the profile, the lane — live in exactly one
    module. A literal reappearing in either caller is the beginning of the next month-long
    divergence, so it fails here rather than in production."""
    # Markers unique to the writer loop. `max_tokens` is deliberately not among them —
    # the scorer's judge is a separate, legitimate model call that also takes one.
    for path in ("persona/teacher/writer.py", "services/tuning/scorer.py"):
        src = (_ROOT / path).read_text()
        for knob in ("run_teacher_tool_loop", "terminal_tools", "max_iterations"):
            assert knob not in src, f"{path} re-invokes the writer loop itself ({knob})"


def test_evaluation_differs_from_production_in_exactly_one_way():
    """Stubbed child executors, and nothing else. The tool surface the model sees — names,
    descriptions, schemas — stays byte-identical, so a replay cannot quietly score a
    different toolset than the one production runs."""
    live = {t.name: t for t in canvas_writer_pass.writer_tools(_USER)}
    stubbed = {t.name: t for t in scorer._stubbed_writer_tools()}

    assert set(live) == set(stubbed) == {"load_guide", "mount_template", "edit_note"}
    for name in live:
        assert live[name].description == stubbed[name].description
        assert live[name].params_schema == stubbed[name].params_schema

    # The authoring children — and only those — become inert recorders.
    assert stubbed["mount_template"].executor is scorer._stub_mount
    assert stubbed["edit_note"].executor is scorer._stub_edit
    # `load_guide` is pure, so it still runs for real: the pick a replay records is the
    # pick a real guide body was actually loaded for.
    assert "=== GUIDE: plot ===" in asyncio.run(
        stubbed["load_guide"].executor({"ids": ["plot"]}))


# ---- the bug, as a table ----------------------------------------------------------------

def test_an_empty_spoken_answer_is_refused_before_the_model_is_called(monkeypatch):
    """THE REPRODUCER. `transcript=""` used to yield ok=false / quality=0.0 / selected=[] —
    a number indistinguishable from a real regression. It is now a named refusal, and it
    costs nothing because it happens before the loop."""
    loop = _FakeLoop()
    monkeypatch.setattr(canvas_writer_pass, "run_teacher_tool_loop", loop)
    with pytest.raises(WriterContractError) as e:
        _run(_inputs(voice_transcript=""))
    assert e.value.name == "voice_transcript"
    assert loop.kwargs is None, "it must refuse before spending anything"


def test_a_blank_question_is_refused_too():
    with pytest.raises(WriterContractError) as e:
        _run(_inputs(question="   "))
    assert e.value.name == "question"


def test_the_same_case_with_a_real_spoken_answer_runs(monkeypatch):
    loop = _FakeLoop([{"kind": "tool_call", "name": "load_guide", "arguments": {"ids": ["plot"]}},
                      {"kind": "done", "text": "", "stop_reason": "end_turn"}])
    monkeypatch.setattr(canvas_writer_pass, "run_teacher_tool_loop", loop)
    out = _run()
    assert out.selected_guides == {"plot"} and out.failed_because is None


# ---- what a run records ------------------------------------------------------------------

def test_the_calls_are_the_result(monkeypatch):
    """A tool call is not a diagnostic — it is the boundary between two tunables, and
    `load_guide(ids)` is what the menu exists to cause. Argument paths match what the tunable
    declares to skillforge, so the record and the declaration name the same thing."""
    loop = _FakeLoop([
        {"kind": "tool_call", "name": "load_guide", "arguments": {"ids": ["plot"]}},
        {"kind": "tool_call", "name": "mount_template",
         "arguments": {"params": {"markdown": "```plot\n{}\n```"}}},
        {"kind": "done", "text": "", "stop_reason": "end_turn"},
    ])
    monkeypatch.setattr(canvas_writer_pass, "run_teacher_tool_loop", loop)
    out = _run()

    assert out.calls == {"load_guide": {"ids": ["plot"]},
                         "mount_template": {"params.markdown": ["```plot\n{}\n```"]}}
    assert out.mount_fired is True
    assert out.authored_parts == ["```plot\n{}\n```"]


def test_a_child_that_was_never_called_is_absent_rather_than_empty(monkeypatch):
    """So "it never opened anything" reads as a fact, not an inference from an empty blob."""
    loop = _FakeLoop([{"kind": "done", "text": "the answer stands on its own",
                       "stop_reason": "end_turn"}])
    monkeypatch.setattr(canvas_writer_pass, "run_teacher_tool_loop", loop)
    assert _run().calls == {}


def test_the_trace_keeps_what_both_loops_used_to_throw_away(monkeypatch):
    """Both loops dropped every non-tool_call event, which is why the model's own explanation
    — "there is no content to mirror" — was discarded. That one sentence is the difference
    between a five-minute diagnosis and a day-long one."""
    loop = _FakeLoop([
        {"kind": "delta", "text": "the spoken answer is empty, "},
        {"kind": "delta", "text": "there's no content to mirror"},
        {"kind": "done", "text": "", "stop_reason": "end_turn", "tool_rounds": 1,
         "deadline_hit": False},
    ])
    monkeypatch.setattr(canvas_writer_pass, "run_teacher_tool_loop", loop)
    trace = _run().trace

    text = [e for e in trace if e["kind"] == "text"]
    assert text and "no content to mirror" in text[0]["text"]
    assert any(e["kind"] == "done" and e["stop_reason"] == "end_turn" for e in trace)
    assert any(e["kind"] == "timing" for e in trace)


def test_a_crash_is_reported_not_raised(monkeypatch):
    """The production caller is a detached task whose originating stream is already closed,
    so the loop's own failures come back as a named result rather than an exception."""
    class _Boom:
        def __call__(self, **kwargs):
            async def _gen():
                raise RuntimeError("LLM down")
                yield  # pragma: no cover
            return _gen()

    monkeypatch.setattr(canvas_writer_pass, "run_teacher_tool_loop", _Boom())
    out = _run()
    assert out.failed_because == "crashed:RuntimeError: LLM down"
    assert any(e["kind"] == "error" for e in out.trace)


def test_the_knobs_are_the_ones_production_needs(monkeypatch):
    """Pinned because they were literals in two files: the 4096 default truncated a note's
    markdown mid-arguments and nothing mounted, and a non-terminal authoring verb produced
    duplicate appends in production."""
    loop = _FakeLoop([{"kind": "done", "text": "", "stop_reason": "end_turn"}])
    monkeypatch.setattr(canvas_writer_pass, "run_teacher_tool_loop", loop)
    _run()
    assert loop.kwargs["max_tokens"] == 8192
    assert loop.kwargs["terminal_tools"] == {"mount_template", "edit_note"}
    assert loop.kwargs["profile"] == "voice"
    assert loop.kwargs["purpose"] == "test"
