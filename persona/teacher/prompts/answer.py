"""Answer-scenario prompt — user asked a question, teacher answers.

Composes from skills + helpers. The output should match the previous
monolithic `prompt_v2.build_answer_prompt` to within whitespace so the
LLM behavior is unchanged across the refactor.
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
    """Build the answer-scenario prompt.

    Same signature as the legacy `build_answer_prompt_v2` so the agent
    dispatch and tool loop need no changes during the refactor.
    """
    # ---- STATIC SYSTEM ---------------------------------------------------
    system_parts: List[str] = [
        "You are a helpful and patient reading assistant. "
        "Please read the teaching principles (loaded below).",
    ]

    teaching_principle = load_skill("teacher/teaching_principle")
    if teaching_principle:
        system_parts.append("")
        system_parts.append(teaching_principle)
    system_parts.append("")

    # Persona-specific canvas framing (the "you control the canvas" voice).
    canvas_persona = load_skill("teacher/canvas_persona")
    if canvas_persona:
        system_parts.append(canvas_persona)
        system_parts.append("")

    # Shared canvas knowledge from the workshop set.
    for skill_name in _CANVAS_SKILLS:
        body = load_skill(skill_name)
        if body:
            system_parts.append(body)
            system_parts.append("")

    # Output contract — TITLE / TONE / MATH / STRUCTURE.
    answer_format = load_skill("teacher/answer_format")
    if answer_format:
        system_parts.append(answer_format)
        system_parts.append("")

    # Preferences + background blocks (cacheable; rare to change).
    system_parts.extend(preferences_block.render(user_profile, self_description))
    system_parts.extend(preferences_block.render_talk_preference(talk_preference))

    # Mastery snapshot (cacheable; updated when concepts decay/recall).
    mastery = summarise_mastery(concept_nodes)
    if mastery:
        system_parts.append("")
        system_parts.append(mastery)

    static_system = "\n".join(system_parts)

    # ---- STATIC USER PASSAGE --------------------------------------------
    static_user_passage = f"=== FULL PASSAGE ===\n{passage}" if passage else ""

    # ---- DYNAMIC USER ---------------------------------------------------
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
