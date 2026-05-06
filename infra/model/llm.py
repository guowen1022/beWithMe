"""LLM facade — exposes the active provider's interface.

Call sites import from here (e.g. `from infra.model.llm import generate`)
and never reach into `minimax/` or `deepseek/` directly. The active
backend is chosen by `settings.llm_provider` at process start, controlled
via the `LLM_PROVIDER` env var.

All providers expose the same five functions:
  - generate(prompt, system="", max_tokens=4096) -> str
  - generate_cached(static_system, static_user_passage, dynamic_user,
                    prior_messages=None, max_tokens=4096) -> (text, usage)
  - stream_cached(...) -> AsyncIterator[{"kind": "delta"|"done", ...}]
  - stream_with_tools(static_system, static_user_passage, dynamic_user,
                      prior_messages=None, tools=None, max_tokens=4096)
                      -> AsyncIterator[{"kind": "delta"|"tool_call"|"done", ...}]
  - generate_json(prompt, max_tokens=512) -> str

This facade adds two optional kwargs to every public method:
  - `purpose: str | None` — scenario name (e.g. "answer", "reflect",
    "router") used as the `trigger` field on the emitted TeacherThinking
    event.
  - `user_id: UUID | None` — routes the event to the right SSE channel.

When both are set, the facade wraps the call with `infra.observability`
to emit start/end TeacherThinking events. Callers that pass neither
(scripts, internal sub-calls) get the original behavior with no event
overhead.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator, Dict, Optional, Tuple
from uuid import UUID

from infra.config import settings
from infra.contracts.ui import TeacherThinking
from infra.observability import emit_thinking

_PROVIDER = (settings.llm_provider or "").lower()


def _require(env_name: str, value: str) -> None:
    if not value:
        raise RuntimeError(
            f"LLM_PROVIDER={_PROVIDER!r} requires {env_name} to be set in .env"
        )


# ---- Provider dispatch --------------------------------------------------

if _PROVIDER == "deepseek":
    _require("DEEPSEEK_API_KEY", settings.deepseek_api_key)
    _require("DEEPSEEK_BASE_URL", settings.deepseek_base_url)
    _require("DEEPSEEK_MODEL", settings.deepseek_model)
    from infra.model.deepseek.llm import (
        generate as _raw_generate,
        generate_cached as _raw_generate_cached,
        stream_cached as _raw_stream_cached,
        stream_with_tools as _raw_stream_with_tools,
        generate_json as _raw_generate_json,
    )
    _MODEL_NAME = settings.deepseek_model
elif _PROVIDER == "minimax":
    _require("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    _require("ANTHROPIC_BASE_URL", settings.anthropic_base_url)
    _require("LLM_MODEL", settings.llm_model)
    from infra.model.minimax.llm import (
        generate as _raw_generate,
        generate_cached as _raw_generate_cached,
        stream_cached as _raw_stream_cached,
        stream_with_tools as _raw_stream_with_tools,
        generate_json as _raw_generate_json,
    )
    _MODEL_NAME = settings.llm_model
elif _PROVIDER == "fake":
    from infra.model.fake.llm import (
        generate as _raw_generate,
        generate_cached as _raw_generate_cached,
        stream_cached as _raw_stream_cached,
        stream_with_tools as _raw_stream_with_tools,
        generate_json as _raw_generate_json,
    )
    _MODEL_NAME = "fake"
else:
    raise ValueError(
        f"Unknown LLM_PROVIDER: {settings.llm_provider!r} "
        "(expected 'minimax', 'deepseek', or 'fake')"
    )


# ---- Observability wrappers --------------------------------------------


def _summarise_prompt(*texts: str) -> str:
    """Pick the most informative bit (the dynamic user message usually)
    and trim to a one-line summary for the debug panel."""
    for t in reversed(texts):
        if not t:
            continue
        head = t.strip().split("\n", 1)[0]
        if len(head) > 100:
            head = head[:97] + "…"
        return head
    return ""


def _prompt_tokens(usage: Optional[dict]) -> Optional[int]:
    if not usage:
        return None
    return (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)


def _completion_tokens(usage: Optional[dict]) -> Optional[int]:
    if not usage:
        return None
    out = usage.get("output_tokens")
    return int(out) if out is not None else None


async def _emit_start(purpose: Optional[str], user_id: Optional[UUID], summary: str) -> None:
    if not purpose or user_id is None:
        return
    await emit_thinking(user_id, TeacherThinking(
        phase="start",
        trigger=purpose,
        summary=summary,
        provider=_PROVIDER,
        model=_MODEL_NAME,
    ))


async def _emit_end(
    purpose: Optional[str],
    user_id: Optional[UUID],
    summary: str,
    started_at: float,
    text: Optional[str] = None,
    usage: Optional[dict] = None,
    tool_calls: Optional[list] = None,
) -> None:
    if not purpose or user_id is None:
        return
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    await emit_thinking(user_id, TeacherThinking(
        phase="end",
        trigger=purpose,
        summary=summary,
        text=(text or "")[:500] if text else None,
        tool_calls=tool_calls or [],
        provider=_PROVIDER,
        model=_MODEL_NAME,
        prompt_tokens=_prompt_tokens(usage),
        completion_tokens=_completion_tokens(usage),
        latency_ms=latency_ms,
    ))


# ---- Public surface — observability-wrapped --------------------------


async def generate(
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
    *,
    purpose: Optional[str] = None,
    user_id: Optional[UUID] = None,
) -> str:
    summary = _summarise_prompt(prompt)
    started = time.perf_counter()
    await _emit_start(purpose, user_id, summary)
    try:
        text = await _raw_generate(prompt, system=system, max_tokens=max_tokens)
    finally:
        await _emit_end(purpose, user_id, summary, started, text=locals().get("text"))
    return text


async def generate_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
    *,
    purpose: Optional[str] = None,
    user_id: Optional[UUID] = None,
) -> Tuple[str, dict]:
    summary = _summarise_prompt(dynamic_user, static_user_passage)
    started = time.perf_counter()
    await _emit_start(purpose, user_id, summary)
    text: str = ""
    usage: dict = {}
    try:
        text, usage = await _raw_generate_cached(
            static_system, static_user_passage, dynamic_user,
            prior_messages=prior_messages, max_tokens=max_tokens,
        )
    finally:
        await _emit_end(purpose, user_id, summary, started, text=text, usage=usage)
    return text, usage


async def stream_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
    *,
    purpose: Optional[str] = None,
    user_id: Optional[UUID] = None,
) -> AsyncIterator[Dict[str, Any]]:
    summary = _summarise_prompt(dynamic_user, static_user_passage)
    started = time.perf_counter()
    await _emit_start(purpose, user_id, summary)
    final_text = ""
    final_usage: dict = {}
    try:
        async for evt in _raw_stream_cached(
            static_system, static_user_passage, dynamic_user,
            prior_messages=prior_messages, max_tokens=max_tokens,
        ):
            if evt.get("kind") == "done":
                final_text = evt.get("text", "")
                final_usage = evt.get("usage") or {}
            yield evt
    finally:
        await _emit_end(purpose, user_id, summary, started, text=final_text, usage=final_usage)


async def stream_with_tools(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    tools=None,
    max_tokens: int = 4096,
    *,
    purpose: Optional[str] = None,
    user_id: Optional[UUID] = None,
) -> AsyncIterator[Dict[str, Any]]:
    summary = _summarise_prompt(dynamic_user, static_user_passage)
    started = time.perf_counter()
    await _emit_start(purpose, user_id, summary)
    final_text_parts: list[str] = []
    final_usage: dict = {}
    tool_calls_seen: list = []
    try:
        async for evt in _raw_stream_with_tools(
            static_system, static_user_passage, dynamic_user,
            prior_messages=prior_messages, tools=tools, max_tokens=max_tokens,
        ):
            kind = evt.get("kind")
            if kind == "delta":
                final_text_parts.append(evt.get("text", ""))
            elif kind == "tool_call":
                tool_calls_seen.append({
                    "name": evt.get("name"),
                    "arguments": evt.get("arguments") or {},
                })
            elif kind == "done":
                if "usage" in evt:
                    final_usage = evt.get("usage") or {}
            yield evt
    finally:
        await _emit_end(
            purpose, user_id, summary, started,
            text="".join(final_text_parts),
            usage=final_usage,
            tool_calls=tool_calls_seen,
        )


async def generate_json(
    prompt: str,
    max_tokens: int = 512,
    *,
    purpose: Optional[str] = None,
    user_id: Optional[UUID] = None,
) -> str:
    summary = _summarise_prompt(prompt)
    started = time.perf_counter()
    await _emit_start(purpose, user_id, summary)
    text = ""
    try:
        text = await _raw_generate_json(prompt, max_tokens=max_tokens)
    finally:
        await _emit_end(purpose, user_id, summary, started, text=text)
    return text


__all__ = [
    "generate",
    "generate_cached",
    "stream_cached",
    "stream_with_tools",
    "generate_json",
]
