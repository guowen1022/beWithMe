"""Lane A voice-mode prompt — sibling of `prompts/answer.py`.

Same signature, same return type. Used when the active TALK CHANNEL on
the requesting device is `voice` or `both`. Differs from `answer.py`
in exactly two ways:

  * `answer_format.md` (TITLE / CONCLUSION FIRST / blocks-with-`---` /
    CONCEPTS) is NOT loaded — that is a *written-text contract*,
    irrelevant for spoken delivery and actively misleading when the
    response is auto-TTS'd sentence by sentence.

  * `lane_a_voice.md` is loaded in its place — plain conversational
    prose, no markdown, no meta-narration, voice-primary with visuals
    as supplements.

The rest of the system prompt (teaching principle, canvas persona,
canvas skills, preferences, talk-preference rule) is identical so the
persona keeps its full canvas tool palette and learner-context
awareness.
"""
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from persona.teacher.prompts import preferences_block
from persona.teacher.prompts.canvas_renderer import format_canvas_state
from persona.teacher.prompts.learner_state import summarise_mastery
from persona.teacher.prompts.parts import PromptParts
from persona.teacher.prompts.skills import load_skill

if TYPE_CHECKING:
    from silicon_brain.models.document import DocumentChunk
    from persona.teacher.preferences.state import UserProfileState
    from persona.teacher.knowledge.models import ConceptNode


_CANVAS_SKILLS = (
    "workshop/canvas/grid",
    "workshop/canvas/lifecycle",
    "workshop/canvas/state_kinds",
    "workshop/canvas/layering",
    "workshop/canvas/tool_verbs",
)


def build(
    passage: Optional[str],
    selected_text: Optional[str],
    question: str,
    self_description: str,
    doc_chunks: List["DocumentChunk"],
    user_profile: Optional["UserProfileState"] = None,
    concept_nodes: Optional[List["ConceptNode"]] = None,
    graph_context: str = "",
    canvas_state: object = None,
    talk_preference: dict | None = None,
) -> PromptParts:
    """Build the voice-mode answer prompt. Same signature as
    `prompts/answer.py:build()` so callers can swap at the
    `assemble_context` layer based on the active channel."""
    system_parts: List[str] = [
        "You are a helpful and patient reading assistant. "
        "Please read the teaching principles (loaded below).",
    ]

    teaching_principle = load_skill("teacher/teaching_principle")
    if teaching_principle:
        system_parts.append("")
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

    # Lane A voice contract — replaces answer_format.md for spoken turns.
    lane_a_voice = load_skill("teacher/lane_a_voice")
    if lane_a_voice:
        system_parts.append(lane_a_voice)
        system_parts.append("")

    system_parts.extend(preferences_block.render(user_profile, self_description))
    system_parts.extend(preferences_block.render_talk_preference(talk_preference))

    mastery = summarise_mastery(concept_nodes)
    if mastery:
        system_parts.append("")
        system_parts.append(mastery)

    static_system = "\n".join(system_parts)

    static_user_passage = f"=== FULL PASSAGE ===\n{passage}" if passage else ""

    dynamic_parts: List[str] = []

    canvas_section = format_canvas_state(canvas_state)
    if canvas_section:
        dynamic_parts.append(canvas_section)

    if graph_context:
        dynamic_parts.append(graph_context)

    if user_profile and user_profile.session_interest_summary:
        dynamic_parts.append(
            f"CURRENT SESSION FOCUS:\n{user_profile.session_interest_summary}"
        )

    if doc_chunks:
        context = "\n---\n".join(c.text for c in doc_chunks)
        dynamic_parts.append(f"=== ADDITIONAL CONTEXT FROM DOCUMENT ===\n{context}")

    if selected_text:
        dynamic_parts.append(
            "=== HIGHLIGHTED TEXT (PRIMARY SUBJECT — the question below refers to this) ===\n"
            f"{selected_text}\n\n"
            "=== QUESTION (about the highlighted text above) ===\n"
            f"{question}"
        )
    else:
        dynamic_parts.append(f"=== QUESTION ===\n{question}")

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage=static_user_passage,
        dynamic_user=dynamic_user,
    )


__all__ = ["build"]
