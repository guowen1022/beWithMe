"""Fake LLM provider for e2e tests.

Activated via `LLM_PROVIDER=fake`. The full ask path runs end-to-end against
the real DB and real persona sidecar — only the LLM call is replaced with
canned, deterministic tokens. No API key, no network, no cost.

Mirrors the surface the facade in `infra/model/llm.py` re-exports:
  generate(prompt, system="", max_tokens=4096) -> str
  generate_cached(static_system, static_user_passage, dynamic_user,
                  prior_messages=None, max_tokens=4096) -> (text, usage)
  stream_cached(...) -> AsyncIterator[{"kind": "delta"|"done", ...}]
  generate_json(prompt, max_tokens=512) -> str
"""
from __future__ import annotations

import json
import uuid
from typing import AsyncIterator, Dict, Any, List, Optional, Tuple

from infra.model.tools import ToolSpec


# A fixed, recognizable answer the e2e tests can assert against.
_FAKE_ANSWER = (
    "TITLE: Fake test answer for e2e\n\n"
    "This is a deterministic response from the fake LLM provider used in tests. "
    "No real model was called.\n\n"
    "CONCEPTS: fake_test_concept"
)
_FAKE_USAGE: Dict[str, Any] = {
    "input_tokens": 0,
    "output_tokens": len(_FAKE_ANSWER.split()),
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}


async def generate(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    return _FAKE_ANSWER


async def generate_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
    disable_thinking: bool = False,
    reasoning_effort: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    return _FAKE_ANSWER, _FAKE_USAGE


async def stream_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
    disable_thinking: bool = False,
    reasoning_effort: Optional[str] = None,
    model: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield deltas word-by-word, then a single 'done' event."""
    # Stream in roughly word-sized chunks so the title parser sees a newline
    # and resolves correctly (the streaming-side fix from earlier).
    text = _FAKE_ANSWER
    # First chunk: through the first newline so parse_title resolves immediately.
    nl = text.find("\n")
    if nl >= 0:
        head = text[: nl + 1]
        rest = text[nl + 1 :]
        yield {"kind": "delta", "text": head}
    else:
        head = ""
        rest = text

    # Then the rest in small chunks.
    chunk_size = 16
    i = 0
    while i < len(rest):
        yield {"kind": "delta", "text": rest[i : i + chunk_size]}
        i += chunk_size

    yield {"kind": "done", "text": text, "usage": _FAKE_USAGE}


async def generate_json(prompt: str, max_tokens: int = 512) -> str:
    return '{"fake": true}'


# Tool-calling scripts — keyed by a substring the test asks the teacher to
# include in its message. A test can drive a deterministic flow by sending
# `dynamic_user="please block_action highlight=hello"` and seeing the
# fake provider emit a block_action tool_call. After the loop returns the
# tool result, a final answer turn fires.
def _scripted_tool_call(text: str, tools: List[ToolSpec]) -> Optional[Dict[str, Any]]:
    by_name = {t.name: t for t in tools}
    lower = (text or "").lower()
    if "list_media" in lower and "list_media" in by_name:
        return {"name": "list_media", "arguments": {}}
    if "request_new_block" in lower and "request_new_block" in by_name:
        return {
            "name": "request_new_block",
            "arguments": {"description": "hello"},
        }
    if "block_action" in lower and "block_action" in by_name:
        # Pull a block id out of the message if present (`block=foo`),
        # else default to the conventional `hello` block.
        block_id = "hello"
        for token in (text or "").split():
            if token.startswith("block="):
                block_id = token.split("=", 1)[1]
        action = "highlight"
        for cand in ("highlight", "focus", "scroll_to"):
            if cand in lower:
                action = cand
        return {
            "name": "block_action",
            "arguments": {"block_id": block_id, "action": action},
        }
    if "push_block_content" in lower and "push_block_content" in by_name:
        return {
            "name": "push_block_content",
            "arguments": {"block_id": "hello", "topic": "greeting", "value": "hi"},
        }
    if "speak" in lower and "speak" in by_name:
        return {
            "name": "speak",
            "arguments": {
                "text": "hello from the fake provider",
                "channel": "voice",
            },
        }
    if "point_arrow" in lower and "point_arrow" in by_name:
        return {
            "name": "point_arrow",
            "arguments": {
                "from_block_id": "hello",
                "to_block_id": "world",
                "label": "links",
            },
        }
    return None


async def stream_with_tools(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    tools: Optional[List[ToolSpec]] = None,
    max_tokens: int = 4096,
    disable_thinking: bool = False,
    reasoning_effort: Optional[str] = None,
    model: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Deterministic tool-calling script for tests.

    First-turn behavior: if the most recent message references a tool name
    by string match, emit one tool_call event and stop with stop_reason
    `tool_use`. Otherwise stream a normal answer like `stream_cached`.

    The tool loop will then run the executor and call us again with the
    tool result appended to `prior_messages`. On follow-up turns we always
    answer (no chained tool calls) so tests have a finite end.
    """
    tool_specs = tools or []

    # Decide based on the most recent user message. The tool loop appends
    # tool results as 'user' role messages with a `tool_result_for=...`
    # marker, so detect that to know we're on a follow-up turn.
    last_text = dynamic_user or ""
    is_follow_up = False
    if prior_messages:
        for msg in reversed(prior_messages):
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, str) and "[tool_result " in content:
                is_follow_up = True
                last_text = content
                break

    if not is_follow_up:
        call = _scripted_tool_call(last_text, tool_specs)
        if call is not None:
            tool_call_id = f"fake-{uuid.uuid4().hex[:8]}"
            yield {
                "kind": "tool_call",
                "id": tool_call_id,
                "name": call["name"],
                "arguments": call["arguments"],
            }
            yield {
                "kind": "done",
                "text": "",
                "usage": _FAKE_USAGE,
                "stop_reason": "tool_use",
            }
            return

    # Either no tool was matched, or this is a follow-up after a tool call:
    # produce the canned text answer.
    text = _FAKE_ANSWER
    nl = text.find("\n")
    if nl >= 0:
        head = text[: nl + 1]
        rest = text[nl + 1:]
        yield {"kind": "delta", "text": head}
    else:
        rest = text
    chunk_size = 16
    i = 0
    while i < len(rest):
        yield {"kind": "delta", "text": rest[i:i + chunk_size]}
        i += chunk_size
    yield {
        "kind": "done",
        "text": text,
        "usage": _FAKE_USAGE,
        "stop_reason": "end_turn",
    }
