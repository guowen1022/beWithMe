"""DeepSeek raw API client.

Mirrors the function surface of `app.infra.model.minimax.llm` so call sites
can swap providers by changing the import path. Uses the OpenAI Python SDK
pointed at https://api.deepseek.com (DeepSeek's OpenAI-compatible endpoint).

Caching: DeepSeek's context caching is automatic prefix-based on the server
side — no client-side cache key needed. Cache hits are reported via
`usage.prompt_cache_hit_tokens` (cached input) vs `prompt_cache_miss_tokens`
(uncached input).
"""
from typing import Optional, Tuple, AsyncIterator, Dict, Any
from openai import AsyncOpenAI
from infra.config import settings

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
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
