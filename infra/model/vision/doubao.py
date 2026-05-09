"""Doubao Seed 2.0 Lite via Volces Ark — OpenAI-compatible vision provider.

Endpoint: https://ark.cn-beijing.volces.com/api/v3
Docs: https://www.volcengine.com/docs/82379/1362931?lang=en

Notes captured during smoke testing:
  * Volces' cn-beijing region cannot fetch external image URLs (Wikipedia,
    etc. time out as geo-blocked). Callers must pass base64 data URLs for
    screenshots — never raw http(s) image links.
  * Minimum image dimension is 14×14 pixels.
  * The response carries `message.reasoning_content` alongside `content`.
    We only return `content` so the chain-of-thought never leaks into the
    persona's context (matches the "no reasoning exposure" rule).
"""
from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI

from infra.config import settings
from infra.model.http_client import make_async_http_client

_client: Optional[AsyncOpenAI] = None

_DEFAULT_PROMPT = "Describe what is shown in this image."


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.doubao_api_key,
            base_url=settings.doubao_base_url,
            http_client=make_async_http_client(),
            max_retries=2,
        )
    return _client


async def describe_image(image: str, prompt: Optional[str] = None) -> str:
    """Send `image` (data URL or http(s) URL) to Doubao and return its description.

    The text-only DeepSeek persona delegates here when it needs visual
    perception. Returns plain text — reasoning trace is dropped.
    """
    client = _get_client()
    completion = await client.chat.completions.create(
        model=settings.doubao_vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or _DEFAULT_PROMPT},
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            }
        ],
    )
    return (completion.choices[0].message.content or "").strip()
