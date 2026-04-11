from typing import Optional, Tuple, AsyncIterator, Dict, Any
import re
import anthropic
from app.config import settings

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        kwargs = {"api_key": settings.anthropic_api_key}
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


async def generate_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    max_tokens: int = 4096,
) -> Tuple[str, dict]:
    """Generate with a single prompt-cache breakpoint at the end of the
    system block.

    Empirically, MiniMax only honors ONE `cache_control` marker per request:
    placing a second breakpoint inside the user message is silently ignored
    and that content is re-tokenized every call. So we fold the static passage
    into the `system` block — the whole static prefix (instructions +
    preferences + background + passage) becomes one cacheable chunk, and the
    user message carries only the volatile tail.

    Returns `(text, usage_dict)`. `usage_dict` exposes
    `cache_creation_input_tokens` / `cache_read_input_tokens` so callers
    (and the frontend debug panel) can see hit rates.
    """
    client = _get_client()

    # Fold the static passage into the system block — see docstring.
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

    # Anthropic requires at least one user content block.
    user_text = dynamic_user if dynamic_user else ""
    messages = [{"role": "user", "content": user_text}]

    kwargs = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system_blocks:
        kwargs["system"] = system_blocks

    response = await client.messages.create(**kwargs)
    raw = _extract_text(response)
    text = _strip_think_tags(raw)
    return text, _usage_dict(response.usage)


async def stream_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    max_tokens: int = 4096,
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

    kwargs: Dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": dynamic_user or ""}],
    }
    if system_blocks:
        kwargs["system"] = system_blocks

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


async def generate_json(prompt: str, max_tokens: int = 512) -> str:
    """Generate a JSON array response. Uses assistant prefill to force text output."""
    # Use a fresh client to avoid state issues with shared client
    kwargs = {"api_key": settings.anthropic_api_key}
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url
    client = anthropic.AsyncAnthropic(**kwargs, timeout=120.0)
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
