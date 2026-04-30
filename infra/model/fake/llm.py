"""Fake LLM provider for e2e tests.

Activated via `LLM_PROVIDER=fake`. The full ask path runs end-to-end against
the real DB and real persona sidecar — only the LLM call is replaced with
canned, deterministic tokens. No API key, no network, no cost.

Mirrors the surface the facade in `infra/model/llm.py` re-exports:
  generate(prompt, system="", max_tokens=4096) -> str
  generate_cached(static_system, static_user_passage, dynamic_user,
                  prior_messages=None, max_tokens=4096) -> (text, usage)
  stream_cached(...) -> AsyncIterator[{"kind": "delta"|"done", ...}]
  generate_json(prompt, max_tokens=512) -> str
"""
from __future__ import annotations

from typing import AsyncIterator, Dict, Any, Optional, Tuple


# A fixed, recognizable answer the e2e tests can assert against.
_FAKE_ANSWER = (
    "TITLE: Fake test answer for e2e\n\n"
    "This is a deterministic response from the fake LLM provider used in tests. "
    "No real model was called.\n\n"
    "CONCEPTS: fake_test_concept"
)
_FAKE_USAGE: Dict[str, Any] = {
    "input_tokens": 0,
    "output_tokens": len(_FAKE_ANSWER.split()),
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}


async def generate(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    return _FAKE_ANSWER


async def generate_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
) -> Tuple[str, Dict[str, Any]]:
    return _FAKE_ANSWER, _FAKE_USAGE


async def stream_cached(
    static_system: str,
    static_user_passage: str,
    dynamic_user: str,
    prior_messages: Optional[list] = None,
    max_tokens: int = 4096,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield deltas word-by-word, then a single 'done' event."""
    # Stream in roughly word-sized chunks so the title parser sees a newline
    # and resolves correctly (the streaming-side fix from earlier).
    text = _FAKE_ANSWER
    # First chunk: through the first newline so parse_title resolves immediately.
    nl = text.find("\n")
    if nl >= 0:
        head = text[: nl + 1]
        rest = text[nl + 1 :]
        yield {"kind": "delta", "text": head}
    else:
        head = ""
        rest = text

    # Then the rest in small chunks.
    chunk_size = 16
    i = 0
    while i < len(rest):
        yield {"kind": "delta", "text": rest[i : i + chunk_size]}
        i += chunk_size

    yield {"kind": "done", "text": text, "usage": _FAKE_USAGE}


async def generate_json(prompt: str, max_tokens: int = 512) -> str:
    return '{"fake": true}'
