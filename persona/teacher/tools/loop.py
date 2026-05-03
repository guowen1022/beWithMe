"""Teacher tool-execution loop.

`run(...)` drives one user turn end-to-end:

  1. Open `stream_with_tools` with the static system prompt + dynamic
     user message + the teacher's tool manifest.
  2. Forward `delta` events to the caller as text tokens.
  3. When the LLM emits `tool_call`s, execute them concurrently, collect
     their results, then re-open the stream with the tool calls + results
     appended as conversation turns. Repeat until the model says it's
     done (`stop_reason != "tool_use"`).
  4. Yield a final `done` event with the assembled answer text + usage.

We pass tool call/result history back to the model as plain user/assistant
messages with a stable text envelope. Both DeepSeek (OpenAI) and MiniMax
(Anthropic) accept that as a degraded-but-functional alternative to the
provider-specific tool-result message types — and crucially it works the
same across providers without each one needing a different message shape.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from infra.model.llm import stream_with_tools
from infra.model.tools import ToolSpec


_MAX_TOOL_TURNS = 6
_TOOL_RESULT_MAX_CHARS = 2000


def _truncate(s: str, n: int = _TOOL_RESULT_MAX_CHARS) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n…[truncated {len(s) - n} chars]"


async def _execute_tool_calls(
    calls: List[Dict[str, Any]], tools: List[ToolSpec]
) -> List[Dict[str, Any]]:
    """Run every tool concurrently. Unknown tools surface as error strings."""
    by_name = {t.name: t for t in tools}

    async def _one(call: Dict[str, Any]) -> Dict[str, Any]:
        spec = by_name.get(call.get("name") or "")
        if spec is None:
            return {"call": call, "result": json.dumps({"error": f"unknown tool {call.get('name')!r}"})}
        try:
            result_text = await spec.executor(call.get("arguments") or {})
        except Exception as e:
            result_text = json.dumps({"error": f"{type(e).__name__}: {e}"})
        return {"call": call, "result": _truncate(result_text)}

    return await asyncio.gather(*(_one(c) for c in calls))


def _format_tool_round_for_history(executed: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Render one tool-use round as plain user/assistant messages.

    The assistant message records what the model asked for; the user
    message echoes back the tool results. Provider-agnostic; both
    DeepSeek and MiniMax happily continue from this transcript.
    """
    asked = "\n".join(
        f"[tool_call name={e['call'].get('name')} id={e['call'].get('id')} args={json.dumps(e['call'].get('arguments') or {})}]"
        for e in executed
    )
    answered = "\n".join(
        f"[tool_result for={e['call'].get('id')} name={e['call'].get('name')}]\n{e['result']}"
        for e in executed
    )
    return [
        {"role": "assistant", "content": asked},
        {"role": "user", "content": answered},
    ]


async def run(
    *,
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[List[Dict[str, Any]]],
    tools: List[ToolSpec],
    max_tokens: int = 4096,
) -> AsyncIterator[Dict[str, Any]]:
    """Drive the tool loop. Yields delta + done events to the caller.

    Final `done` shape:
      {"kind": "done", "text": full_answer, "usage": last_usage,
       "stop_reason": "end_turn", "tool_rounds": int}
    """
    history: List[Dict[str, Any]] = list(prior_messages or [])
    full_text_parts: List[str] = []
    last_usage: Dict[str, Any] = {}
    tool_rounds = 0

    for turn in range(_MAX_TOOL_TURNS + 1):
        pending_calls: List[Dict[str, Any]] = []
        stop_reason = "end_turn"

        async for evt in stream_with_tools(
            static_system,
            static_user_passage,
            dynamic_user,
            prior_messages=history,
            tools=tools,
            max_tokens=max_tokens,
        ):
            kind = evt.get("kind")
            if kind == "delta":
                full_text_parts.append(evt.get("text", ""))
                yield {"kind": "delta", "text": evt.get("text", "")}
            elif kind == "tool_call":
                pending_calls.append({
                    "id": evt.get("id"),
                    "name": evt.get("name"),
                    "arguments": evt.get("arguments") or {},
                })
            elif kind == "done":
                last_usage = evt.get("usage", {}) or {}
                stop_reason = evt.get("stop_reason") or "end_turn"

        if not pending_calls or stop_reason != "tool_use":
            break
        if turn >= _MAX_TOOL_TURNS:
            # Hard cap — surface a final answer turn anyway, but stop
            # asking the model to call more tools.
            break

        executed = await _execute_tool_calls(pending_calls, tools)
        tool_rounds += 1
        history.extend(_format_tool_round_for_history(executed))
        # Subsequent turns continue the same conversation — the user's
        # original `dynamic_user` shouldn't be re-sent. Move it into
        # history once and clear it for follow-ups.
        if dynamic_user:
            history.append({"role": "user", "content": dynamic_user})
            dynamic_user = ""

    yield {
        "kind": "done",
        "text": "".join(full_text_parts),
        "usage": last_usage,
        "stop_reason": "end_turn",
        "tool_rounds": tool_rounds,
    }


__all__ = ["run"]
