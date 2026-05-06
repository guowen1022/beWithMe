"""Learner-state summary for the system prompt.

Renders the user's concept-mastery snapshot into a stable section that
goes into the cacheable `static_system` portion of every scenario
prompt. Same across answer/reflect/etc.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from infra.hlr import compute_mastery, mastery_to_state

if TYPE_CHECKING:
    from persona.teacher.knowledge.models import ConceptNode


def summarise_mastery(
    concept_nodes: Optional[List["ConceptNode"]],
) -> str:
    """Bucket concepts by mastery state and render a stable summary
    section. Empty string when there's nothing to say.
    """
    if not concept_nodes:
        return ""

    now = datetime.utcnow()
    by_state: dict[str, list[str]] = {}
    for node in concept_nodes:
        ref_time = node.last_recalled_at or node.last_seen
        if ref_time and ref_time.tzinfo is not None:
            ref_time = ref_time.replace(tzinfo=None)
        hours_since = max(0, (now - ref_time).total_seconds() / 3600.0)
        p = compute_mastery(node.half_life_hours, hours_since)
        state = mastery_to_state(p)
        by_state.setdefault(state, []).append(node.name)

    if not by_state:
        return ""

    lines = ["USER'S CONCEPT KNOWLEDGE:"]
    for state in ("solid", "learning", "rusty", "faded"):
        if state in by_state:
            names = ", ".join(by_state[state][:10])
            lines.append(f"- {state}: {names}")
    return "\n".join(lines)


__all__ = ["summarise_mastery"]
