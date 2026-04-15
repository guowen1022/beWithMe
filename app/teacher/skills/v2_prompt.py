"""v2 prompt builder — experimental dynamic system prompt.

Same interface as app.teacher.prompt.build_answer_prompt so it can be
swapped in without touching the LLM layer.
"""

from typing import Optional, List
from datetime import datetime
from app.teacher.prompt import PromptParts
from app.silicon_brain.models.document import DocumentChunk
from app.silicon_brain.user_profile.state import UserProfileState
from app.silicon_brain.knowledge.models import ConceptNode
from app.silicon_brain.knowledge.hlr import compute_mastery, mastery_to_state


def build_answer_prompt(
    passage: Optional[str],
    selected_text: Optional[str],
    question: str,
    self_description: str,
    doc_chunks: List[DocumentChunk],
    user_profile: Optional[UserProfileState] = None,
    concept_nodes: Optional[List[ConceptNode]] = None,
    graph_context: str = "",
) -> PromptParts:
    """Build the answer prompt v2 — more dynamic system instructions.

    Key differences from v1:
    - Concept mastery is woven into the system prompt (not just dynamic_user)
    - Tone adapts based on mastery distribution (more beginner-friendly vs peer-level)
    - Shorter, more opinionated instructions
    """

    # ---- Analyse learner state to set tone --------------------------------
    mastery_summary = ""
    beginner_mode = True  # default if no concept data
    if concept_nodes:
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

        solid = len(by_state.get("solid", []))
        total = sum(len(v) for v in by_state.values())
        beginner_mode = total < 5 or (solid / max(total, 1)) < 0.3

        if by_state:
            lines = []
            for state in ["solid", "learning", "rusty", "faded"]:
                if state in by_state:
                    names = ", ".join(by_state[state][:10])
                    lines.append(f"- {state}: {names}")
            mastery_summary = "USER'S CONCEPT KNOWLEDGE:\n" + "\n".join(lines)

    # ---- STATIC SYSTEM (cacheable) ----------------------------------------
    if beginner_mode:
        tone = (
            "The user is still building foundations. "
            "Use simple language, define jargon inline, and connect new ideas to everyday analogies."
        )
    else:
        tone = (
            "The user has solid grounding. "
            "Be concise and precise — skip basics they already know, go deeper on nuance."
        )

    system_parts = [
        "You are a reading assistant that adapts to the learner.",
        "",
        f"TONE: {tone}",
        "",
        "RULES:",
        "- 3-6 sentences, ~120 words max. Short sentences (~15 words each).",
        "- No preamble, no restating the question, no closing summary.",
        "- If highlighted text is provided, it is the PRIMARY SUBJECT. Resolve pronouns against it.",
        "- Draw on your full knowledge — the passage is context, not a boundary.",
        "- For multi-turn sessions, prior turns are live context the user can see.",
        "- If you don't know something, say so honestly.",
        "",
        "OUTPUT FORMAT:",
        "- First line: TITLE: <one-line summary, max 60 chars, no trailing punctuation>",
        "- Blank line, then answer body.",
        "- Last line: CONCEPTS: concept1, concept2, concept3 (1-5 domain terms from your answer).",
    ]

    if user_profile:
        style_map = {
            "explanation_style": "Explanation style",
            "depth_preference": "Depth",
            "analogy_affinity": "Use of analogies",
            "math_comfort": "Math comfort level",
            "pacing": "Pacing",
        }
        pref_lines = []
        for key, label in style_map.items():
            val = getattr(user_profile, key, None)
            if val and val != "moderate" and val != "balanced":
                pref_lines.append(f"- {label}: {val}")
        if user_profile.meta_notes:
            pref_lines.append(f"- Notes: {user_profile.meta_notes}")

        if pref_lines:
            system_parts.append("")
            system_parts.append("LEARNER PREFERENCES:")
            system_parts.extend(pref_lines)

    if self_description:
        system_parts.append(f"\nUSER BACKGROUND:\n{self_description}")

    # v2: mastery snapshot in the system prompt so it's cached across
    # follow-up questions in the same session (concepts change slowly).
    if mastery_summary:
        system_parts.append("")
        system_parts.append(mastery_summary)

    static_system = "\n".join(system_parts)

    # ---- STATIC USER PASSAGE (cacheable) ----------------------------------
    static_user_passage = f"=== FULL PASSAGE ===\n{passage}" if passage else ""

    # ---- DYNAMIC USER (not cached) ----------------------------------------
    dynamic_parts: list[str] = []

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
