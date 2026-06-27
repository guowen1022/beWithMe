"""Lead-pass prompt — the fast front-line response. Even leaner than `voice_answer.py`.

Used when `BWM_LEAD=1` and the active channel is voice. The lead pass is a
fast quick-judgment + dispatcher: it gives an immediate, accurate response and
carries one routing tool (`request_handoff`) to hand off deeper work. A
separate pass (`canvas_writer`, or the deep answering pass) does the slower
work after the lead line streams. So this prompt:

  * loads `lead_brief.md` (never-disclaim + brevity rule) instead of
    `lane_a_voice.md`
  * skips the canvas skills entirely — the lead pass doesn't act on the canvas
  * skips `canvas_persona` — irrelevant on the fast line
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

    lead_brief = load_skill("teacher/lead_brief")
    if lead_brief:
        system_parts.append(lead_brief)
        system_parts.append("")

    # Lead-pass routing direction: answer now, hand off deep (a closer
    # look/action), or hand off to session control — via request_handoff.
    lead_routing = load_skill("teacher/lead_routing")
    if lead_routing:
        system_parts.append(lead_routing)
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
