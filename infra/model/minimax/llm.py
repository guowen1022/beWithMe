from typing import Optional, Tuple, AsyncIterator, Dict, Any, List
import json
import re
import anthropic
from infra.config import settings
from infra.model.http_client import make_async_http_client
from infra.model.tools import ToolSpec

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        # Custom httpx client with keepalive_expiry + read/connect timeouts
        # so idle pool connections don't hang the next call after a long
        # idle gap. See infra/model/http_client.py for rationale.
        kwargs = {
            "api_key": settings.anthropic_api_key,
            "http_client": make_async_http_client(),
            "max_retries": 3,
        }
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        _client = anthropic.AsyncAnthropic(**kwargs)
    return _client


def _strip_think_tags(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_text(response) -> str:
    """Extract only text blocks. Never use thinking blocks as output."""
    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(text_parts)


async def generate(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Generate a response. Only returns text blocks."""
    client = _get_client()
    messages = [{"role": "user", "content": prompt}]
    kwargs = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    response = await client.messages.create(**kwargs)
    raw = _extract_text(response)
    return _strip_think_tags(raw)


def _usage_dict(usage) -> dict:
    """Flatten an Anthropic `usage` object into a JSON-serialisable dict.

    Missing fields (e.g., cache_* when the provider doesn't support caching)
    default to 0 so the frontend can render them unconditionally.
    """
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def _build_request(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    """Assemble the Anthropic request body shared by streaming and non-streaming.

    The cache breakpoint sits on the system block. MiniMax only honors ONE
    `cache_control` marker per request, so the entire static prefix
    (instructions + preferences + background + passage) folds into the system
    block as one cacheable chunk. Prior session turns are passed verbatim as
    plain user/assistant messages so the LLM sees one continuous chat —
    they are not cached, since the suffix grows turn by turn.
    """
    if static_user_passage:
        full_static = (
            (static_system + "\n\n" if static_system else "") + static_user_passage
        )
    else:
        full_static = static_system

    system_blocks = []
    if full_static:
        system_blocks.append({
            "type": "text",
            "text": full_static,
            "cache_control": {"type": "ephemeral"},
        })

    messages: list = list(prior_messages or [])
    messages.append({"role": "user", "content": dynamic_user or ""})

    kwargs: Dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system_blocks:
        kwargs["system"] = system_blocks
    return kwargs


async def generate_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
    disable_thinking: bool = False,
    reasoning_effort: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[str, dict]:
    """Generate with a single prompt-cache breakpoint at the end of the
    system block. See `_build_request` for the request shape.

    Returns `(text, usage_dict)`. `usage_dict` exposes
    `cache_creation_input_tokens` / `cache_read_input_tokens` so callers
    (and the frontend debug panel) can see hit rates.
    """
    client = _get_client()
    kwargs = _build_request(
        static_system, static_user_passage, dynamic_user, prior_messages, max_tokens
    )
    response = await client.messages.create(**kwargs)
    raw = _extract_text(response)
    text = _strip_think_tags(raw)
    return text, _usage_dict(response.usage)


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
    """Streaming variant of `generate_cached`.

    Yields dicts:
      - {"kind": "delta", "text": "..."} for each text_delta from the LLM
      - {"kind": "done", "text": full_text, "usage": {...}} once at the end

    MiniMax emits model thinking as a separate content block (`thinking_delta`),
    so iterating only `text_delta` events yields clean answer text — no regex
    think-tag stripping needed. Note: text deltas only start AFTER the
    thinking block completes, so for reasoning-heavy questions the user
    still waits for the reasoning phase before tokens flow.
    """
    client = _get_client()
    kwargs = _build_request(
        static_system, static_user_passage, dynamic_user, prior_messages, max_tokens
    )

    full_text_parts: list[str] = []
    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            if getattr(event, "type", None) != "content_block_delta":
                continue
            delta = getattr(event, "delta", None)
            if delta is None:
                continue
            if getattr(delta, "type", None) == "text_delta":
                chunk = getattr(delta, "text", "") or ""
                if chunk:
                    full_text_parts.append(chunk)
                    yield {"kind": "delta", "text": chunk}

        final = await stream.get_final_message()
        # Defensive: if the SDK surfaces text only via final message, fall back.
        full_text = "".join(full_text_parts)
        if not full_text:
            full_text = _extract_text(final)
        yield {
            "kind": "done",
            "text": full_text,
            "usage": _usage_dict(final.usage),
        }


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
    """Streaming chat with Anthropic-style tool calling.

    Tool-use blocks arrive as `content_block_start` (with the tool name
    + id), then a sequence of `content_block_delta` events with type
    `input_json_delta` carrying string fragments of the JSON arguments.
    We accumulate per-block-index, then emit one `tool_call` event when
    the whole block closes.

    Yield shape matches the contract in `infra.model.tools`, plus
    `{"kind": "thinking", "text": ...}` for each `thinking_delta`. MiniMax
    returns reasoning as its own content block; we forward it verbatim and keep
    it OUT of `full_text_parts` (it is never answer text — see `_extract_text`).
    Only what the provider actually sent is emitted, so a build with thinking
    off produces no `thinking` events rather than a reconstructed one.
    """
    client = _get_client()
    kwargs = _build_request(
        static_system, static_user_passage, dynamic_user, prior_messages, max_tokens
    )
    if tools:
        kwargs["tools"] = [t.to_anthropic() for t in tools]

    full_text_parts: list[str] = []
    # index → {"id": str, "name": str, "arguments_raw": str}
    tool_buckets: Dict[int, Dict[str, Any]] = {}
    pending_tool_yields: list[Dict[str, Any]] = []

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            etype = getattr(event, "type", None)

            if etype == "content_block_start":
                idx = getattr(event, "index", 0) or 0
                block = getattr(event, "content_block", None)
                if block is not None and getattr(block, "type", None) == "tool_use":
                    tool_buckets[idx] = {
                        "id": getattr(block, "id", "") or f"call_{idx}",
                        "name": getattr(block, "name", "") or "",
                        "arguments_raw": "",
                    }

            elif etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                dt = getattr(delta, "type", None)
                if dt == "text_delta":
                    chunk = getattr(delta, "text", "") or ""
                    if chunk:
                        full_text_parts.append(chunk)
                        yield {"kind": "delta", "text": chunk}
                elif dt == "thinking_delta":
                    chunk = getattr(delta, "thinking", "") or ""
                    if chunk:
                        yield {"kind": "thinking", "text": chunk}
                elif dt == "input_json_delta":
                    idx = getattr(event, "index", 0) or 0
                    if idx in tool_buckets:
                        partial = getattr(delta, "partial_json", "") or ""
                        tool_buckets[idx]["arguments_raw"] += partial

            elif etype == "content_block_stop":
                idx = getattr(event, "index", 0) or 0
                if idx in tool_buckets:
                    b = tool_buckets[idx]
                    try:
                        args = json.loads(b["arguments_raw"]) if b["arguments_raw"] else {}
                    except json.JSONDecodeError:
                        args = {"_raw_arguments": b["arguments_raw"]}
                    pending_tool_yields.append({
                        "kind": "tool_call",
                        "id": b["id"],
                        "name": b["name"],
                        "arguments": args,
                    })

        final = await stream.get_final_message()

    for evt in pending_tool_yields:
        yield evt

    full_text = "".join(full_text_parts)
    if not full_text:
        full_text = _strip_think_tags(_extract_text(final))

    raw_stop = getattr(final, "stop_reason", None) or "end_turn"
    stop = "tool_use" if raw_stop == "tool_use" else raw_stop
    yield {
        "kind": "done",
        "text": full_text,
        "usage": _usage_dict(final.usage),
        "stop_reason": stop,
    }


async def generate_json(prompt: str, max_tokens: int = 512) -> str:
    """Generate a JSON array response. Uses assistant prefill to force text output."""
    # Use a fresh client to avoid state issues with shared client.
    # Same keepalive/timeout/retry hygiene as the long-lived client —
    # this one-shot path is rare but should still survive a stale pool.
    kwargs = {
        "api_key": settings.anthropic_api_key,
        "http_client": make_async_http_client(),
        "max_retries": 3,
    }
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url
    client = anthropic.AsyncAnthropic(**kwargs)
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=max_tokens,
        system="You extract structured data. Respond with ONLY the requested JSON array. No explanations.",
        messages=[
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": '["'},
        ],
    )
    # Debug: log all response blocks
    for i, block in enumerate(response.content):
        bt = block.type
        bt_text = getattr(block, 'text', '')[:80] if hasattr(block, 'text') else ''
        bt_think = (getattr(block, 'thinking', '') or '')[:80]
        print(f"[generate_json] block[{i}] type={bt} text={bt_text!r} think={bt_think!r}", flush=True)
    raw = _extract_text(response)
    raw = _strip_think_tags(raw)
    # Strip markdown fences if present
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if match:
            raw = match.group(1).strip()
    # Prepend the prefill we started with
    if raw and not raw.startswith("["):
        raw = '["' + raw
    return raw.strip()
