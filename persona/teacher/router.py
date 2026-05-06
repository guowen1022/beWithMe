"""Teacher intent router.

A thin LLM-based classifier that decides whether the user's message
wants a UI block built (engineer delegation) or a normal answer.
Loaded skill: `teacher/router`.

The router runs *before* the heavy answer pipeline, so it has to be
cheap and fast — small prompt, JSON-mode output, low max_tokens.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Optional
from uuid import UUID

from infra.model.llm import generate_json
from workshop import load_skill


@dataclass(frozen=True)
class RouterDecision:
    intent: Literal["ui_block", "answer"]
    description: str = ""


def _build_prompt(question: str) -> str:
    # Keep the prompt small. The skill already has the rules and
    # examples; we only inject the user message.
    skill = load_skill("teacher/router")
    return f"{skill}\n\nUser message:\n\n{question}\n\nReturn JSON now."


async def route(question: str, user_id: Optional[UUID] = None) -> RouterDecision:
    """Classify the user's message. Defaults to 'answer' on any failure."""
    if not question or not question.strip():
        return RouterDecision(intent="answer")
    try:
        raw = await generate_json(
            _build_prompt(question), max_tokens=200,
            purpose="router", user_id=user_id,
        )
        data = json.loads(raw)
        intent = data.get("intent")
        if intent == "ui_block":
            description = (data.get("description") or question).strip()
            return RouterDecision(intent="ui_block", description=description)
        return RouterDecision(intent="answer")
    except Exception as e:
        print(f"[teacher.router] fallback to 'answer' on error: {e}", flush=True)
        return RouterDecision(intent="answer")
