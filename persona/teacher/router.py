"""Teacher intent router.

A thin LLM-based classifier that decides whether the user's message wants
a UI block built (engineer delegation) or a normal answer. Loaded skill:
`persona/teacher/skills/router.md`.

The router runs *before* the heavy answer pipeline, so it has to be cheap
and fast — small prompt, JSON-mode output, low max_tokens.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from infra.model.llm import generate_json

_SKILLS_DIR = Path(__file__).parent / "skills"
_ROUTER_SKILL_PATH = _SKILLS_DIR / "router.md"


@dataclass(frozen=True)
class RouterDecision:
    intent: Literal["ui_block", "answer"]
    description: str = ""


def _load_router_skill() -> str:
    if _ROUTER_SKILL_PATH.exists():
        return _ROUTER_SKILL_PATH.read_text(encoding="utf-8").strip()
    return ""


_ROUTER_SKILL = _load_router_skill()


def _build_prompt(question: str) -> str:
    # Keep the prompt small. The skill already has the rules and examples;
    # we only inject the user message.
    return f"{_ROUTER_SKILL}\n\nUser message:\n\n{question}\n\nReturn JSON now."


async def route(question: str) -> RouterDecision:
    """Classify the user's message. Defaults to 'answer' on any failure."""
    if not question or not question.strip():
        return RouterDecision(intent="answer")
    try:
        raw = await generate_json(_build_prompt(question), max_tokens=200)
        data = json.loads(raw)
        intent = data.get("intent")
        if intent == "ui_block":
            description = (data.get("description") or question).strip()
            return RouterDecision(intent="ui_block", description=description)
        return RouterDecision(intent="answer")
    except Exception as e:
        print(f"[teacher.router] fallback to 'answer' on error: {e}", flush=True)
        return RouterDecision(intent="answer")
