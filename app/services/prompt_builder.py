from typing import Optional, List, NamedTuple
from app.models.interaction import Interaction
from app.models.document import DocumentChunk
from app.user_profile.state import UserProfileState
from app.knowledge.models import ConceptNode
from app.knowledge.hlr import compute_mastery, mastery_to_state
from datetime import datetime


class PromptParts(NamedTuple):
    """Structured prompt split into cacheable and volatile sections.

    - `static_system`: instructions + user background + stable preferences.
      Changes only when preferences are re-distilled. Goes into the Anthropic
      `system` field with cache_control.
    - `static_user_passage`: the passage the user is reading. Constant across
      all questions in a session. First content block of the user message,
      marked with cache_control.
    - `dynamic_user`: concept mastery, graph context, similar past interactions,
      selected text, doc chunks, and the question itself. Anything that changes
      per question — never cached.
    """
    static_system: str
    static_user_passage: str
    dynamic_user: str


def build_answer_prompt(
    passage: Optional[str],
    selected_text: Optional[str],
    question: str,
    self_description: str,
    similar_interactions: List[Interaction],
    doc_chunks: List[DocumentChunk],
    user_profile: Optional[UserProfileState] = None,
    concept_nodes: Optional[List[ConceptNode]] = None,
    graph_context: str = "",
) -> PromptParts:
    """Build the answer prompt in three parts so the LLM layer can apply
    prompt caching to the static prefix.

    See `PromptParts` for the split rationale.
    """

    # ---- STATIC SYSTEM (cacheable) ---------------------------------------
    system_parts = [
        "You are a knowledgeable reading assistant. The user is reading a passage and has a question.",
        "",
        "HOW TO ANSWER:",
        "1. The passage tells you WHAT the user is reading. Use it to understand the topic and context.",
        "2. If the user highlighted specific text, that's what they're curious about.",
        "3. Use your FULL KNOWLEDGE to answer — explain, expand, provide background, examples, and connections.",
        "4. If the question needs current/specific facts beyond your knowledge or training data, you can browse the web to find up-to-date information.",
        "5. The passage is the starting point, not the boundary. Draw on everything you know about the topic.",
        "6. Past interactions show what the user has studied before — use them to personalize, not as the answer topic.",
        "7. If the user message includes a USER'S CONCEPT KNOWLEDGE block, build on what they already know and explain unfamiliar concepts more carefully.",
        "8. At the VERY END of your answer, add a line: CONCEPTS: concept1, concept2, concept3 — listing 1-5 domain knowledge concepts covered in your answer (textbook-level terms only, no generic words).",
    ]

    # Stable preferences live in the cached prefix. They change rarely
    # (only when the distiller runs) so caching them is a net win.
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
            system_parts.append("USER'S LEARNING PREFERENCES (adapt your answer to match):")
            system_parts.extend(pref_lines)

    if self_description:
        system_parts.append(f"\nUSER BACKGROUND:\n{self_description}")

    static_system = "\n".join(system_parts)

    # ---- STATIC USER PASSAGE (cacheable) ---------------------------------
    static_user_passage = f"=== FULL PASSAGE ===\n{passage}" if passage else ""

    # ---- DYNAMIC USER (not cached) ---------------------------------------
    dynamic_parts: list[str] = []

    # Concept mastery snapshot — changes every question as concepts get
    # extracted / recalled / decayed.
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
        if by_state:
            concept_lines = ["USER'S CONCEPT KNOWLEDGE (what they've studied before):"]
            for state in ["solid", "learning", "rusty", "faded"]:
                if state in by_state:
                    names = ", ".join(by_state[state][:10])
                    concept_lines.append(f"- {state}: {names}")
            dynamic_parts.append("\n".join(concept_lines))

    if graph_context:
        dynamic_parts.append(graph_context)

    if user_profile and user_profile.session_interest_summary:
        dynamic_parts.append(
            f"CURRENT SESSION FOCUS:\n{user_profile.session_interest_summary}"
        )

    if selected_text:
        dynamic_parts.append(
            f"=== HIGHLIGHTED TEXT (user is asking about this part) ===\n{selected_text}"
        )

    if doc_chunks:
        context = "\n---\n".join(c.text for c in doc_chunks)
        dynamic_parts.append(f"=== ADDITIONAL CONTEXT FROM DOCUMENT ===\n{context}")

    if similar_interactions:
        past = []
        for i in similar_interactions:
            entry = f"Q: {i.question}\nA: {i.answer[:200]}"
            past.append(entry)
        dynamic_parts.append(
            "=== BACKGROUND: user's past questions (for reference only) ===\n"
            + "\n---\n".join(past)
        )

    dynamic_parts.append(f"=== QUESTION ===\n{question}")

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage=static_user_passage,
        dynamic_user=dynamic_user,
    )
