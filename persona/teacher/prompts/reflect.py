"""Reflect-scenario prompt — perception event woke the teacher.

Used by the trigger pipeline for ALL three perception event types
(`BlockCompletedEvent`, `BlockChangeEvent`, `VoiceEvent`). The events
list carries the type so the LLM can weight a `completed` event more
heavily than a `changed` one.

Drops the answer-format skill — reflect turns are not user-visible
answers; their text appears only in the developer debug panel as a
"thinking" note. Most reflect turns should produce zero tool calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, List, Optional, TYPE_CHECKING

from persona.teacher.prompts import preferences_block
from persona.teacher.prompts.canvas_renderer import format_canvas_state
from persona.teacher.prompts.learner_state import summarise_mastery
from persona.teacher.prompts.parts import PromptParts
from persona.teacher.prompts.skills import load_skill

if TYPE_CHECKING:
    from persona.teacher.preferences.state import UserProfileState
    from persona.teacher.knowledge.models import ConceptNode


_CANVAS_SKILLS = (
    "workshop/canvas/grid",
    "workshop/canvas/lifecycle",
    "workshop/canvas/state_kinds",
    "workshop/canvas/layering",
    "workshop/canvas/tool_verbs",
)


@dataclass(frozen=True)
class PerceptionEventSummary:
    """A perception event normalised for prompt rendering. The trigger
    pipeline builds these from `BlockCompletedEvent` / `BlockChangeEvent`
    / `VoiceEvent` so the prompt module doesn't depend on the cache
    contracts directly."""
    event_type: str            # "completed" | "changed" | "voice"
    block_id: Optional[str]    # None for voice
    state_kind: Optional[str]  # state.kind for block events
    content: Optional[str]     # state.content excerpt for blocks; text for voice
    extra: Optional[dict] = None


def _format_events(events: Iterable[PerceptionEventSummary]) -> str:
    lines: List[str] = []
    for e in events:
        bits = [f"- [{e.event_type}]"]
        if e.block_id:
            bits.append(f"block_id={e.block_id}")
        if e.state_kind:
            bits.append(f"kind={e.state_kind}")
        if e.content:
            content = e.content.strip().replace("\n", " ")
            if len(content) > 80:
                content = content[:77] + "…"
            bits.append(f"content={content!r}")
        if e.extra:
            try:
                bits.append(f"extra={json.dumps(e.extra, default=str)}")
            except Exception:
                bits.append(f"extra={e.extra!r}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def build(
    events: List[PerceptionEventSummary],
    canvas_state: object = None,
    user_profile: Optional["UserProfileState"] = None,
    concept_nodes: Optional[List["ConceptNode"]] = None,
    self_description: str = "",
) -> PromptParts:
    """Build the reflect-scenario prompt.

    `events` is the (already coalesced) batch of perception events that
    triggered this turn. The reflect_policy.md skill tells the LLM how
    to read them: act on deterministic next steps, otherwise stay
    silent.
    """
    # ---- STATIC SYSTEM ---------------------------------------------------
    system_parts: List[str] = []

    teaching_principle = load_skill("teacher/teaching_principle")
    if teaching_principle:
        system_parts.append(teaching_principle)
        system_parts.append("")

    canvas_persona = load_skill("teacher/canvas_persona")
    if canvas_persona:
        system_parts.append(canvas_persona)
        system_parts.append("")

    for skill_name in _CANVAS_SKILLS:
        body = load_skill(skill_name)
        if body:
            system_parts.append(body)
            system_parts.append("")

    reflect_policy = load_skill("teacher/reflect_policy")
    if reflect_policy:
        system_parts.append(reflect_policy)
        system_parts.append("")

    system_parts.extend(preferences_block.render(user_profile, self_description))

    mastery = summarise_mastery(concept_nodes)
    if mastery:
        system_parts.append("")
        system_parts.append(mastery)

    static_system = "\n".join(system_parts)

    # ---- DYNAMIC USER ---------------------------------------------------
    # Reflect turns have no passage and no question. The user message
    # carries the canvas snapshot + the events that fired.
    dynamic_parts: List[str] = []

    canvas_section = format_canvas_state(canvas_state)
    if canvas_section:
        dynamic_parts.append(canvas_section)

    events_section = _format_events(events)
    if events_section:
        dynamic_parts.append(f"=== PERCEPTION UPDATES ===\n{events_section}")
    else:
        # Defensive: trigger fired with no events somehow. Tell the LLM
        # so it doesn't hallucinate a justification.
        dynamic_parts.append("=== PERCEPTION UPDATES ===\n(no events in this batch)")

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage="",
        dynamic_user=dynamic_user,
    )


__all__ = ["build", "PerceptionEventSummary"]
