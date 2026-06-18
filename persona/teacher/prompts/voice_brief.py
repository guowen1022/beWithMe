"""Voice-leads voice prompt — even leaner than `voice_answer.py`.

Used when `BWM_VOICE_LEADS=1` and the active channel is voice. The voice
call has NO tools — a separate `canvas_writer` pass paints the canvas
after the spoken answer completes. So this prompt:

  * loads `voice_brief.md` (strict brevity + no-padding rule) instead of
    `lane_a_voice.md`
  * skips the canvas skills entirely — there are no canvas tools to use
  * skips `canvas_persona` — irrelevant when the model can't act on the canvas
  * keeps teaching_principle, preferences, talk_preference, and mastery so
    the spoken answer is still tailored to the user

Same `build()` signature as `voice_answer.py` so the caller can swap
between them on a flag.
"""
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from persona.teacher.prompts import preferences_block
from persona.teacher.prompts.learner_state import summarise_mastery
from persona.teacher.prompts.parts import PromptParts
from persona.teacher.prompts.skills import load_skill

if TYPE_CHECKING:
    from silicon_brain.models.document import DocumentChunk
    from persona.teacher.preferences.state import UserProfileState
    from persona.teacher.knowledge.models import ConceptNode


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
    system_parts: List[str] = [
        "You are a helpful and patient reading assistant. "
        "Please read the teaching principles (loaded below).",
    ]

    teaching_principle = load_skill("teacher/teaching_principle")
    if teaching_principle:
        system_parts.append("")
        system_parts.append(teaching_principle)
    system_parts.append("")

    voice_brief = load_skill("teacher/voice_brief")
    if voice_brief:
        system_parts.append(voice_brief)
        system_parts.append("")

    # Stage-1 routing direction: decide in-teaching-loop vs out-of-loop
    # (session action) and hand off via request_session_control if out.
    session_routing = load_skill("teacher/session_routing")
    if session_routing:
        system_parts.append(session_routing)
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

    # Canvas state is intentionally omitted — this pass cannot act on it,
    # and mentioning what's already mounted invites the model to narrate
    # the visual layer instead of answering the question.

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
