"""The loop's event stream is the only record an offline reader ever gets.

`infra/model/agent_loop.run` is upstream of every trace beWithMe sends to skillforge, so
whatever it declines to yield is unrecoverable downstream — no amount of care in the recorder
can put back an event that was never emitted.

Two things it used to swallow, and both hid a whole class of failure:

  * **tool results.** They were forwarded for exactly one tool (`browser_set` snapshots) and
    only AFTER the terminal-tool break — so the canvas writer, whose `mount_template` is
    terminal, emitted none at all. What a child returned is an input to the rest of the same
    turn: the note is authored FROM the guide body `load_guide` handed back, and without it the
    second half of a run has no visible cause.
  * **model reasoning.** DeepSeek returns it on `reasoning_content` and MiniMax as
    `thinking_delta`; both were read past. It is the single most valuable line in a failed run
    and it was never in the process at all.

The rule these tests hold: filter by what you cannot store, never by kind.
"""
import asyncio
import json
from typing import Any, Dict, List

import pytest

from infra.model import agent_loop
from infra.model.tools import ToolSpec


def _tool(name: str, returns: str) -> ToolSpec:
    async def _run(args):
        return returns
    return ToolSpec(name=name, description="", params_schema={}, executor=_run)


def _raising_tool(name: str) -> ToolSpec:
    async def _run(args):
        raise RuntimeError("guide store offline")
    return ToolSpec(name=name, description="", params_schema={}, executor=_run)


class _FakeProvider:
    """Stands in for `stream_with_tools`: replays one scripted turn per call."""

    def __init__(self, turns: List[List[Dict[str, Any]]]):
        self._turns = list(turns)

    def __call__(self, *a, **kw):
        events = self._turns.pop(0) if self._turns else [
            {"kind": "done", "text": "", "usage": {}, "stop_reason": "end_turn"}]

        async def _gen():
            for e in events:
                yield e
        return _gen()


def _drive(monkeypatch, turns, tools, **kw) -> List[Dict[str, Any]]:
    monkeypatch.setattr(agent_loop, "stream_with_tools", _FakeProvider(turns))

    async def _collect():
        return [e async for e in agent_loop.run(
            static_system="s", static_user_passage="", dynamic_user="u",
            prior_messages=None, tools=tools, **kw)]
    return asyncio.run(_collect())


_CALL_TURN = [
    {"kind": "tool_call", "id": "c1", "name": "load_guide", "arguments": {"ids": ["plot"]}},
    {"kind": "done", "text": "", "usage": {}, "stop_reason": "tool_use"},
]


# ---- results reach the caller ---------------------------------------------------------

def test_every_tool_result_is_yielded_not_just_browser_snapshots(monkeypatch):
    events = _drive(monkeypatch, [_CALL_TURN], [_tool("load_guide", "=== GUIDE: plot ===")])

    results = [e for e in events if e["kind"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["name"] == "load_guide" and results[0]["ok"] is True
    assert results[0]["text"] == "=== GUIDE: plot ==="
    assert results[0]["id"] == "c1"


def test_a_terminal_tools_result_is_yielded_before_the_loop_breaks(monkeypatch):
    """THE writer's case. `mount_template` is terminal, and the result propagation used to sit
    two statements below the break — so the one call the writer exists to make recorded no
    response, ever."""
    turn = [
        {"kind": "tool_call", "id": "c1", "name": "mount_template",
         "arguments": {"params": {"markdown": "# note"}}},
        {"kind": "done", "text": "", "usage": {}, "stop_reason": "tool_use"},
    ]
    events = _drive(monkeypatch, [turn], [_tool("mount_template", "mounted")],
                    terminal_tools={"mount_template"})

    assert [e["kind"] for e in events] == ["tool_call", "tool_result", "done"]
    assert events[1]["text"] == "mounted"


def test_a_child_that_failed_says_so(monkeypatch):
    """`ok` separates "the child answered" from "the child errored" — otherwise a run that
    broke inside a tool is indistinguishable from one that merely produced a bad answer."""
    events = _drive(monkeypatch, [_CALL_TURN], [_raising_tool("load_guide")])
    result = [e for e in events if e["kind"] == "tool_result"][0]

    assert result["ok"] is False
    assert "guide store offline" in result["text"]


def test_a_tool_that_returns_an_error_payload_is_not_ok(monkeypatch):
    events = _drive(monkeypatch, [_CALL_TURN],
                    [_tool("load_guide", json.dumps({"error": "no such guide"}))])
    assert [e for e in events if e["kind"] == "tool_result"][0]["ok"] is False


def test_an_unknown_tool_is_not_ok(monkeypatch):
    events = _drive(monkeypatch, [_CALL_TURN], [])
    result = [e for e in events if e["kind"] == "tool_result"][0]
    assert result["ok"] is False and "unknown tool" in result["text"]


def test_json_results_that_are_not_errors_stay_ok(monkeypatch):
    events = _drive(monkeypatch, [_CALL_TURN],
                    [_tool("load_guide", json.dumps({"guide": "plot", "body": "…"}))])
    assert [e for e in events if e["kind"] == "tool_result"][0]["ok"] is True


def test_browser_snapshots_still_carry_the_parsed_tree(monkeypatch):
    """Lane R anchors recipe @ref tokens against the FIRST snapshot's refs, so broadening the
    emission must not cost the one consumer that already existed its structured payload."""
    turn = [
        {"kind": "tool_call", "id": "c1", "name": "browser_set",
         "arguments": {"action": "snapshot"}},
        {"kind": "done", "text": "", "usage": {}, "stop_reason": "tool_use"},
    ]
    events = _drive(monkeypatch, [turn],
                    [_tool("browser_set", json.dumps({"refs": ["e1", "e2"]}))])
    result = [e for e in events if e["kind"] == "tool_result"][0]

    assert result["action"] == "snapshot"
    assert result["result"]["refs"] == ["e1", "e2"]


# ---- reasoning reaches the caller -----------------------------------------------------

def test_provider_reasoning_is_forwarded(monkeypatch):
    turn = [
        {"kind": "thinking", "text": "the spoken answer is empty, "},
        {"kind": "thinking", "text": "there's no content to mirror"},
        {"kind": "delta", "text": "Nothing to draw."},
        {"kind": "done", "text": "Nothing to draw.", "usage": {}, "stop_reason": "end_turn"},
    ]
    events = _drive(monkeypatch, [turn], [])

    assert [e["text"] for e in events if e["kind"] == "thinking"] == [
        "the spoken answer is empty, ", "there's no content to mirror"]


def test_reasoning_is_never_folded_into_the_answer(monkeypatch):
    """Storing reasoning is not showing it. `done.text` is what the product speaks, so hidden
    reasoning must not leak into it — the trace keeps a separate event instead."""
    turn = [
        {"kind": "thinking", "text": "mermaid or plot?"},
        {"kind": "delta", "text": "Plot it is."},
        {"kind": "done", "text": "Plot it is.", "usage": {}, "stop_reason": "end_turn"},
    ]
    events = _drive(monkeypatch, [turn], [])
    done = [e for e in events if e["kind"] == "done"][0]

    assert done["text"] == "Plot it is."
    assert "mermaid or plot" not in done["text"]


# ---- the providers actually produce it ------------------------------------------------

def test_deepseek_reads_reasoning_content_and_only_what_arrived():
    """DeepSeek's thinking mode puts chain-of-thought on `reasoning_content`, beside `content`
    rather than inside it. Reading past it is why no beWithMe trace could ever answer *why*."""
    from infra.model.deepseek.llm import _reasoning_chunk

    class _Delta:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    assert _reasoning_chunk(_Delta(reasoning_content="because…")) == "because…"
    assert _reasoning_chunk(_Delta(reasoning="because…")) == "because…"
    # No reasoning returned → nothing to record. An absent event is honest.
    assert _reasoning_chunk(_Delta(content="hello")) == ""
    assert _reasoning_chunk(_Delta(reasoning_content=None)) == ""
