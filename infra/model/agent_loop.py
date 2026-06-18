"""Generic tool-execution loop — shared across personas (teacher, app_operator, …).

Relocated from `persona/teacher/tools/loop.py` (which now re-exports `run`
from here) so a second persona can drive the same loop without importing
teacher internals. Depends only on `infra.*`.

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
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import UUID

from infra.config import settings
from infra.model.llm import stream_with_tools
from infra.model.tools import ToolSpec
from infra.model.authz import CapabilityGrant, authorize


_MAX_TOOL_TURNS = 6
_TOOL_RESULT_MAX_CHARS = 2000


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n…[truncated {len(s) - n} chars]"


async def _execute_tool_calls(
    calls: List[Dict[str, Any]],
    tools: List[ToolSpec],
    truncate_chars: int,
    grant: Optional[CapabilityGrant] = None,
) -> List[Dict[str, Any]]:
    """Run every tool concurrently. Unknown tools surface as error strings.

    Each entry has `result` (truncated for the model's context) and
    `result_raw` (untruncated, for callers that need to introspect the
    structured payload — e.g. recipe-recording needs the full snapshot
    refs list which is too big to fit in `result`)."""
    by_name = {t.name: t for t in tools}

    async def _one(call: Dict[str, Any]) -> Dict[str, Any]:
        spec = by_name.get(call.get("name") or "")
        if spec is None:
            err = json.dumps({"error": f"unknown tool {call.get('name')!r}"})
            return {"call": call, "result": err, "result_raw": err}
        # Dispatch-time authorization (defense in depth — assembly already
        # filtered). A persona may select a tool only if its domain is granted.
        if grant is not None and not authorize(grant, spec):
            err = json.dumps({
                "error": (
                    f"tool {spec.name!r} (domain {spec.domain.value}) not in "
                    f"persona {grant.persona!r} grant"
                )
            })
            return {"call": call, "result": err, "result_raw": err}
        try:
            result_text = await spec.executor(call.get("arguments") or {})
        except Exception as e:
            result_text = json.dumps({"error": f"{type(e).__name__}: {e}"})
        return {
            "call": call,
            "result": _truncate(result_text, truncate_chars),
            "result_raw": result_text,
        }

    return await asyncio.gather(*(_one(c) for c in calls))


def _format_tool_round_for_history(executed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Render one tool-use round in the active provider's native shape.

    Why this matters: in early versions we used a plain-text envelope
    (`[tool_call name=... args=...]` / `[tool_result for=... ]`) for
    cross-provider portability. After several rounds the LLM mimics
    that format and starts emitting tool calls as **literal text**
    instead of using the proper tool-use channel — a fatal failure
    mode for long agentic loops (Lane R can run 25 rounds). Native
    tool/tool-call messages eliminate the mimicry path entirely.

    DeepSeek (OpenAI-compatible) format:
      assistant.tool_calls = [{id, type:"function", function:{name, arguments(str)}}]
      tool message: {role:"tool", tool_call_id, content}

    MiniMax (Anthropic) format:
      assistant.content = [{type:"tool_use", id, name, input}, ...]
      user.content = [{type:"tool_result", tool_use_id, content}, ...]
    """
    provider = (settings.llm_provider or "").lower()

    if provider in ("deepseek", "openai"):
        return _format_for_openai(executed)
    if provider in ("minimax", "anthropic"):
        return _format_for_anthropic(executed)

    # Fallback for unknown providers — keep the legacy plain-text shape
    # so we degrade gracefully rather than crash.
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


def _format_for_openai(executed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OpenAI-style: one assistant message with tool_calls + N tool
    messages with tool_call_id."""
    tool_calls = []
    for e in executed:
        c = e["call"]
        tool_calls.append({
            "id": c.get("id") or "call_unknown",
            "type": "function",
            "function": {
                "name": c.get("name") or "",
                "arguments": json.dumps(c.get("arguments") or {}),
            },
        })
    msgs: List[Dict[str, Any]] = [
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
    ]
    for e in executed:
        c = e["call"]
        msgs.append({
            "role": "tool",
            "tool_call_id": c.get("id") or "call_unknown",
            "content": e["result"],
        })
    return msgs


def _format_for_anthropic(executed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Anthropic-style: assistant tool_use blocks + user tool_result blocks."""
    asst_blocks = []
    for e in executed:
        c = e["call"]
        asst_blocks.append({
            "type": "tool_use",
            "id": c.get("id") or "call_unknown",
            "name": c.get("name") or "",
            "input": c.get("arguments") or {},
        })
    user_blocks = []
    for e in executed:
        c = e["call"]
        user_blocks.append({
            "type": "tool_result",
            "tool_use_id": c.get("id") or "call_unknown",
            "content": e["result"],
        })
    return [
        {"role": "assistant", "content": asst_blocks},
        {"role": "user", "content": user_blocks},
    ]


async def run(
    *,
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[List[Dict[str, Any]]],
    tools: List[ToolSpec],
    max_tokens: int = 4096,
    max_iterations: int = _MAX_TOOL_TURNS,
    purpose: Optional[str] = None,
    user_id: Optional[UUID] = None,
    wall_clock_deadline_s: Optional[float] = None,
    deadline_grace_s: float = 10.0,
    tool_result_max_chars: int = _TOOL_RESULT_MAX_CHARS,
    phases: Optional[Dict[str, Any]] = None,
    timing_origin: Optional[float] = None,
    disable_thinking: bool = False,
    profile: Optional[str] = None,
    terminal_tools: Optional[set] = None,
    grant: Optional[CapabilityGrant] = None,  # persona capability — gates tool selection (§4.4)
) -> AsyncIterator[Dict[str, Any]]:
    """Drive the tool loop. Yields delta + done events to the caller.

    `max_iterations` caps the number of tool-result → re-prompt rounds.
    Default is the historical 6 (suitable for `/ask` and Lane B background
    work). Lane A (user-facing reflect) passes 1 to short-circuit the
    loop and answer quickly. Research lane (Lane R) passes ~25 so an
    investigator can chain many tool calls before synthesizing.

    `wall_clock_deadline_s` is a soft deadline measured from the start
    of the call. When set, the loop:
      - Injects a one-time "you have ~N s left, wrap up now" system
        note before the iteration that crosses (deadline - grace_s),
        so the model lands a clean synthesis instead of being chopped
        off mid-tool-call. `deadline_grace_s` is the size of that
        wrap-up window — Lane R uses ~30 s because a tool loop needs
        a couple of rounds to converge on a final `speak`.
      - Hard-breaks if the deadline is exceeded.

    `tool_result_max_chars` controls how aggressively tool results are
    truncated before re-entering the model's context. Lane A keeps the
    tight 2000 default to defend its small token budget; Lane R passes
    a larger value (~6000) so browser pages and document chunks survive
    intact for reasoning.

    `terminal_tools` makes named tools end the loop the instant they execute.
    The canvas writer passes `{"mount_template", "edit_note"}` so it can take
    one or more *non-terminal* `load_guide` rounds (counting toward
    `max_iterations`) and then exactly ONE authoring round — re-prompting
    after an authoring call produced duplicate appends. A terminal round is
    also exempt from the iteration cap, so the final authoring call always
    lands even when guide-loading used the budget.

    Final `done` shape:
      {"kind": "done", "text": full_answer, "usage": last_usage,
       "stop_reason": "end_turn", "tool_rounds": int,
       "deadline_hit": bool}
    """
    history: List[Dict[str, Any]] = list(prior_messages or [])
    full_text_parts: List[str] = []
    last_usage: Dict[str, Any] = {}
    tool_rounds = 0
    started_at = time.monotonic()
    deadline_warned = False
    deadline_hit = False
    # Track whether the last executed round was entirely _raw_arguments bails
    # (provider streamed truncated JSON so the executor returned an error without
    # doing anything). In that case we owe the model one free retry turn beyond
    # max_iterations — the real tool call never happened.
    _last_round_was_bail = False

    # Benchmark instrumentation. `timing_origin` is a perf_counter() reading
    # taken by the caller at request start, so first-token and first-speak
    # timings are reported relative to the user-visible request boundary, not
    # to when this loop happened to run.
    _origin = timing_origin if timing_origin is not None else time.perf_counter()
    _first_delta_seen = False
    _first_speak_seen = False

    # TEMP debug — record the EXACT dynamic_user (which carries
    # `=== CURRENTLY ON CANVAS ===`) the LLM is about to see, plus the
    # last 200 chars of the static system prompt so we can confirm it's
    # not stale-cached. Tee to /tmp/bewithme-perception-trace.log.
    try:
        import time as _t
        _path = "/tmp/bewithme-perception-trace.log"
        ts = _t.strftime("%H:%M:%S")
        sep = "=" * 60
        lines = [
            f"{ts} {sep}",
            f"{ts} [llm-prompt] dynamic_user (full, {len(dynamic_user or '')} chars):",
            *[f"{ts} | {ln}" for ln in (dynamic_user or '').splitlines()[:80]],
            f"{ts} [llm-prompt] static_system tail: ...{(static_system or '')[-200:]!r}",
            f"{ts} [llm-prompt] prior_messages: {len(history)} turns",
        ]
        for ln in lines:
            print(ln, flush=True)
        try:
            with open(_path, "a") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass
    except Exception:
        pass

    for turn in range(max_iterations + 1):
        # Wall-clock deadline check, before opening the next stream.
        # 10 s grace window: inject a one-time system note so the model
        # knows to wrap up. Past the deadline: break (don't even open
        # another stream). The "deadline_hit" flag in done lets callers
        # surface a "had to stop early" hint to the user if needed.
        if wall_clock_deadline_s is not None:
            elapsed = time.monotonic() - started_at
            if elapsed >= wall_clock_deadline_s:
                deadline_hit = True
                break
            if (
                not deadline_warned
                and elapsed >= max(0.0, wall_clock_deadline_s - deadline_grace_s)
            ):
                deadline_warned = True
                remaining = max(1, int(wall_clock_deadline_s - elapsed))
                history.append({
                    "role": "user",
                    "content": (
                        f"[system] DEADLINE: only ~{remaining}s left. "
                        "STOP all investigation tool calls (browser_set, "
                        "read_url, look_at_image). On your VERY NEXT turn "
                        "call `speak` with your synthesis, quoting the "
                        "concrete facts already in your research_note "
                        "history. Do not call any other tool. If you "
                        "feel notes are incomplete, hedge — 'based on "
                        "what I observed' — but DO speak."
                    ),
                })

        pending_calls: List[Dict[str, Any]] = []
        stop_reason = "end_turn"

        async for evt in stream_with_tools(
            static_system,
            static_user_passage,
            dynamic_user,
            prior_messages=history,
            tools=tools,
            max_tokens=max_tokens,
            purpose=purpose,
            user_id=user_id,
            disable_thinking=disable_thinking,
            profile=profile,
        ):
            kind = evt.get("kind")
            if kind == "delta":
                text_chunk = evt.get("text", "")
                if phases is not None and not _first_delta_seen and text_chunk:
                    phases["llm_ttft_ms"] = round((time.perf_counter() - _origin) * 1000, 2)
                    _first_delta_seen = True
                full_text_parts.append(text_chunk)
                yield {"kind": "delta", "text": text_chunk}
            elif kind == "tool_call":
                call_name = evt.get("name")
                if phases is not None and not _first_speak_seen and call_name == "speak":
                    phases["first_speak_call_ms"] = round((time.perf_counter() - _origin) * 1000, 2)
                    phases["tool_iterations_before_speak"] = tool_rounds
                    # The text the persona is about to vocalize — needed by
                    # the benchmark to POST it to /api/speak/stream and
                    # measure the actual TTS first-byte time.
                    args = evt.get("arguments") or {}
                    spoken = args.get("text") or args.get("content") or ""
                    if spoken:
                        phases["first_speak_text"] = spoken
                    _first_speak_seen = True
                pending_calls.append({
                    "id": evt.get("id"),
                    "name": call_name,
                    "arguments": evt.get("arguments") or {},
                })
                # Re-yield to the caller so trigger pipelines can observe
                # which tools the model is invoking (used by Lane R to
                # detect when speak has been called).
                yield {
                    "kind": "tool_call",
                    "id": evt.get("id"),
                    "name": call_name,
                    "arguments": evt.get("arguments") or {},
                }
            elif kind == "done":
                last_usage = evt.get("usage", {}) or {}
                stop_reason = evt.get("stop_reason") or "end_turn"

        # TEMP debug — record what the LLM said this turn + any tool calls.
        try:
            import time as _t
            _path = "/tmp/bewithme-perception-trace.log"
            ts = _t.strftime("%H:%M:%S")
            answer_text = "".join(full_text_parts)
            tool_summary = ", ".join(
                f"{c.get('name')}({list((c.get('arguments') or {}).keys())})"
                for c in pending_calls
            ) if pending_calls else "(none)"
            lines = [
                f"{ts} [llm-response] turn={turn} stop_reason={stop_reason} tool_calls={tool_summary}",
                f"{ts} [llm-response] text ({len(answer_text)} chars): {answer_text[:500]!r}",
            ]
            for ln in lines:
                print(ln, flush=True)
            try:
                with open(_path, "a") as f:
                    f.write("\n".join(lines) + "\n")
            except Exception:
                pass
        except Exception:
            pass

        if not pending_calls or stop_reason != "tool_use":
            break
        # A round containing a terminal tool (e.g. the writer's mount/edit) is
        # exempt from the iteration cap so the final authoring call always
        # lands even if `load_guide` rounds consumed the budget.
        is_terminal_round = bool(terminal_tools) and any(
            c.get("name") in terminal_tools for c in pending_calls
        )
        if turn >= max_iterations and not is_terminal_round:
            # Hard cap — unless the previous round was entirely _raw_arguments
            # bails (provider streamed truncated JSON; executor returned an error
            # but did nothing). That round didn't consume a real iteration, so
            # let the model's retry through once.
            if not _last_round_was_bail:
                break
            _last_round_was_bail = False  # only one free pass per bail

        executed = await _execute_tool_calls(
            pending_calls, tools, tool_result_max_chars, grant=grant
        )
        # Detect whether this round was entirely _raw_arguments bails.
        # Only True when the call had _raw_arguments AND the executor returned
        # an error (recovery path would have returned ok, not an error).
        _last_round_was_bail = bool(executed) and all(
            "_raw_arguments" in (e.get("call") or {}).get("arguments", {})
            and '"error"' in (e.get("result") or "")
            for e in executed
        )
        tool_rounds += 1
        # Terminal-tool round: stop ONLY when a terminal authoring call
        # (mount/edit) actually SUCCEEDED. Re-prompting after a real mount would
        # let the model fire a SECOND one (duplicate appends / highlight spam).
        # But if the terminal call BAILED on truncated `_raw_arguments` (the
        # provider cut the JSON mid-stream), nothing was authored — fall through
        # so the model gets its retry, exactly like the non-terminal bail path.
        # Breaking on a bail here silently drops the note ("writer invoked,
        # nothing shows"). Non-terminal rounds (e.g. load_guide) also fall
        # through and continue the loop.
        terminal_succeeded = bool(terminal_tools) and any(
            (e.get("call") or {}).get("name") in terminal_tools
            and not (
                "_raw_arguments" in ((e.get("call") or {}).get("arguments") or {})
                and '"error"' in (e.get("result") or "")
            )
            for e in executed
        )
        if terminal_succeeded:
            break
        # Propagate snapshot results upstream so callers can capture
        # ARIA refs at record time (used by workshop/research recipes).
        # Other tool results are intentionally NOT propagated — the
        # truncated text already feeds back into the model on the next
        # turn, and snapshot is the only result whose structured shape
        # external callers need to introspect.
        for e in executed:
            call = e.get("call") or {}
            if call.get("name") != "browser_set":
                continue
            args = call.get("arguments") or {}
            if (args.get("action") or "").lower() != "snapshot":
                continue
            # Use `result_raw` (untruncated) — the truncated `result` is
            # mid-JSON when the snapshot tree is large (Wikipedia ≈ 24 KB)
            # and would fail to parse.
            raw = e.get("result_raw") or e.get("result") or "{}"
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            yield {
                "kind": "tool_result",
                "name": "browser_set",
                "action": "snapshot",
                "result": parsed,
            }
        history.extend(_format_tool_round_for_history(executed))
        # Subsequent turns continue the same conversation — the user's
        # original `dynamic_user` shouldn't be re-sent. Move it into
        # history once and clear it for follow-ups.
        if dynamic_user:
            history.append({"role": "user", "content": dynamic_user})
            dynamic_user = ""

    if phases is not None:
        phases["tool_iterations_total"] = tool_rounds
        phases["llm_done_ms"] = round((time.perf_counter() - _origin) * 1000, 2)

    yield {
        "kind": "done",
        "text": "".join(full_text_parts),
        "usage": last_usage,
        "stop_reason": "end_turn",
        "tool_rounds": tool_rounds,
        "deadline_hit": deadline_hit,
    }


__all__ = ["run"]
