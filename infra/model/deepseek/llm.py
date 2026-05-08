"""DeepSeek raw API client.

Mirrors the function surface of `app.infra.model.minimax.llm` so call sites
can swap providers by changing the import path. Uses the OpenAI Python SDK
pointed at https://api.deepseek.com (DeepSeek's OpenAI-compatible endpoint).

Caching: DeepSeek's context caching is automatic prefix-based on the server
side — no client-side cache key needed. Cache hits are reported via
`usage.prompt_cache_hit_tokens` (cached input) vs `prompt_cache_miss_tokens`
(uncached input).
"""
import json
from typing import Optional, Tuple, AsyncIterator, Dict, Any, List
from openai import AsyncOpenAI
from infra.config import settings
from infra.model.http_client import make_async_http_client
from infra.model.tools import ToolSpec

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        # Use a custom httpx client with keepalive_expiry + explicit
        # timeouts. Without this, an idle 5–15 min gap leaves a stale
        # TCP connection in the pool; the next call writes to it and
        # hangs forever waiting for a response. See infra/model/http_client.py.
        _client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            http_client=make_async_http_client(),
            max_retries=3,
        )
    return _client


def _usage_dict(usage) -> dict:
    """Map DeepSeek's OpenAI-style usage to the same shape minimax returns.

    Anthropic splits prompt tokens into three buckets (input + cache_read +
    cache_creation). DeepSeek splits into two (miss + hit). Mapping:
      - input_tokens         ← prompt_cache_miss_tokens (uncached portion)
      - cache_read           ← prompt_cache_hit_tokens (cached portion)
      - cache_creation       ← 0 (DeepSeek doesn't expose newly-written tokens)
    Falls back to prompt_tokens if the cache breakdown is absent.
    """
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    prompt_total = getattr(usage, "prompt_tokens", 0) or 0
    cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    cache_miss = getattr(usage, "prompt_cache_miss_tokens", None)
    input_tokens = cache_miss if cache_miss is not None else max(prompt_total - cache_hit, 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cache_hit,
    }


async def generate(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    client = _get_client()
    messages: list = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=max_tokens,
        messages=messages,
    )
    return response.choices[0].message.content or ""


def _build_messages(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list],
) -> list:
    full_static = (
        (static_system + "\n\n" if static_system else "") + (static_user_passage or "")
    ).strip()
    messages: list = []
    if full_static:
        messages.append({"role": "system", "content": full_static})
    messages.extend(prior_messages or [])
    messages.append({"role": "user", "content": dynamic_user or ""})
    return messages


async def generate_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
) -> Tuple[str, dict]:
    client = _get_client()
    messages = _build_messages(static_system, static_user_passage, dynamic_user, prior_messages)
    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=max_tokens,
        messages=messages,
    )
    text = response.choices[0].message.content or ""
    return text, _usage_dict(response.usage)


async def stream_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
) -> AsyncIterator[Dict[str, Any]]:
    """Streaming variant. Yields delta/done dicts matching minimax's shape."""
    client = _get_client()
    messages = _build_messages(static_system, static_user_passage, dynamic_user, prior_messages)

    full_text_parts: list[str] = []
    final_usage = None
    stream = await client.chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=max_tokens,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.usage is not None:
            final_usage = chunk.usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, "content", None)
        if text:
            full_text_parts.append(text)
            yield {"kind": "delta", "text": text}

    yield {
        "kind": "done",
        "text": "".join(full_text_parts),
        "usage": _usage_dict(final_usage),
    }


async def stream_with_tools(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    tools: Optional[List[ToolSpec]] = None,
    max_tokens: int = 4096,
) -> AsyncIterator[Dict[str, Any]]:
    """Streaming chat with OpenAI-style tool calling.

    Yield shape (matches infra.model.tools docstring):
      {"kind": "delta", "text": "..."}
      {"kind": "tool_call", "id": "...", "name": "...", "arguments": {...}}
      {"kind": "done", "text": full_text, "usage": {...}, "stop_reason": "..."}

    Tool-call deltas arrive piece-meal in OpenAI's streaming format
    (`delta.tool_calls[i]` with growing `function.arguments`). We
    accumulate per-index buckets and emit one `tool_call` event once the
    stream finishes (finish_reason == "tool_calls"). The model can request
    multiple tools in one turn — we yield one event per accumulated call.
    """
    client = _get_client()
    messages = _build_messages(static_system, static_user_passage, dynamic_user, prior_messages)

    kwargs: Dict[str, Any] = {
        "model": settings.deepseek_model,
        "max_tokens": max_tokens,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        kwargs["tools"] = [t.to_openai() for t in tools]

    full_text_parts: list[str] = []
    final_usage = None
    finish_reason: Optional[str] = None
    # index → {"id": str | None, "name": str | None, "arguments": str}
    tool_buckets: Dict[int, Dict[str, Any]] = {}

    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        if chunk.usage is not None:
            final_usage = chunk.usage
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason

        delta = choice.delta
        text = getattr(delta, "content", None)
        if text:
            full_text_parts.append(text)
            yield {"kind": "delta", "text": text}

        tcs = getattr(delta, "tool_calls", None) or []
        for tc in tcs:
            idx = getattr(tc, "index", 0) or 0
            bucket = tool_buckets.setdefault(
                idx, {"id": None, "name": None, "arguments": ""}
            )
            if getattr(tc, "id", None):
                bucket["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    bucket["name"] = fn.name
                if getattr(fn, "arguments", None):
                    bucket["arguments"] += fn.arguments

    for idx in sorted(tool_buckets):
        b = tool_buckets[idx]
        try:
            args = json.loads(b["arguments"]) if b["arguments"] else {}
        except json.JSONDecodeError:
            # Malformed JSON — surface what we got so the caller can choose
            # to retry or abort. The tool loop should treat this as a hard
            # failure rather than guessing.
            args = {"_raw_arguments": b["arguments"]}
        yield {
            "kind": "tool_call",
            "id": b["id"] or f"call_{idx}",
            "name": b["name"] or "",
            "arguments": args,
        }

    stop = "tool_use" if finish_reason == "tool_calls" else (finish_reason or "end_turn")
    yield {
        "kind": "done",
        "text": "".join(full_text_parts),
        "usage": _usage_dict(final_usage),
        "stop_reason": stop,
    }


async def generate_json(prompt: str, max_tokens: int = 512) -> str:
    """Request a JSON object using DeepSeek's native JSON mode.

    Returns the raw JSON text. Caller still parses.
    """
    client = _get_client()
    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You extract structured data. Respond with JSON only."},
            {"role": "user", "content": prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()
