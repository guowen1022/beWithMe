"""PromptParts contract + history helpers.

Shared between every scenario builder. Lives here (not in a builder
module) because the LLM facade and the agent loop both depend on it.
"""
from __future__ import annotations

import re
from typing import List, NamedTuple, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from persona.teacher.models.interaction import Interaction


_TITLE_RE = re.compile(r"^\s*TITLE:\s*(.+?)\s*(?:\n+|$)", re.IGNORECASE)
_CONCEPTS_RE = re.compile(r"\n*\s*CONCEPTS:\s*[^\n]*\s*$", re.IGNORECASE)


class PromptParts(NamedTuple):
    """Structured prompt split into cacheable and volatile sections.

    - `static_system`: instructions + user background + stable preferences.
      Goes into the Anthropic `system` field with cache_control.
    - `static_user_passage`: the passage the user is reading. Constant
      across all questions in a session. First content block of the user
      message, marked with cache_control.
    - `dynamic_user`: anything that changes per turn — concept mastery,
      graph context, doc chunks, selected text, question. Never cached.
    """
    static_system: str
    static_user_passage: str
    dynamic_user: str


def parse_title(answer: str) -> Tuple[Optional[str], str]:
    """Extract the leading TITLE: line emitted by the model.

    Returns `(title, body)`. Title capped at 200 chars to fit the DB
    column. Returns `(None, answer)` when no TITLE line is present.
    """
    m = _TITLE_RE.match(answer)
    if not m:
        return None, answer
    title = m.group(1).strip().rstrip(".!?")[:200]
    body = answer[m.end():]
    return title, body


def clean_answer_for_history(answer: str) -> str:
    """Strip TITLE: and CONCEPTS: lines so historical assistant turns
    sent back to the LLM contain only the prose the user actually saw.
    """
    _, body = parse_title(answer)
    return _CONCEPTS_RE.sub("", body).strip()


def build_history_messages(prior_interactions: List["Interaction"]) -> List[dict]:
    """Map a chronologically-ordered list of prior session interactions
    into Anthropic messages: alternating user/assistant turns with the
    metadata stripped from the assistant text.
    """
    msgs: List[dict] = []
    for i in prior_interactions:
        msgs.append({"role": "user", "content": i.question})
        msgs.append({
            "role": "assistant",
            "content": clean_answer_for_history(i.answer) or "(empty)",
        })
    return msgs


__all__ = [
    "PromptParts",
    "parse_title",
    "clean_answer_for_history",
    "build_history_messages",
]
