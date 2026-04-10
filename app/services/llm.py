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
