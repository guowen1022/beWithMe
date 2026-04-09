from typing import Optional
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
    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(text_parts)


async def generate(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Simple generate — let the proxy handle everything internally."""
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
