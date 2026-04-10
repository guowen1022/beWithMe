from typing import Optional, List, Tuple
from app.models.interaction import Interaction
from app.models.document import DocumentChunk
from app.distill.state import LearnerState


def build_answer_prompt(
    passage: Optional[str],
    selected_text: Optional[str],
    question: str,
    self_description: str,
    similar_interactions: List[Interaction],
    doc_chunks: List[DocumentChunk],
    learner: Optional[LearnerState] = None,
) -> Tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""

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
    ]

    if learner:
        style_map = {
            "explanation_style": "Explanation style",
            "depth_preference": "Depth",
            "analogy_affinity": "Use of analogies",
            "math_comfort": "Math comfort level",
            "pacing": "Pacing",
        }
        pref_lines = []
        for key, label in style_map.items():
            val = getattr(learner, key, None)
            if val and val != "moderate" and val != "balanced":
                pref_lines.append(f"- {label}: {val}")
        if learner.meta_notes:
            pref_lines.append(f"- Notes: {learner.meta_notes}")

        if pref_lines:
            system_parts.append("")
            system_parts.append("USER'S LEARNING PREFERENCES (adapt your answer to match):")
            system_parts.extend(pref_lines)

        if learner.concepts:
            by_state = {}
            for c in learner.concepts:
                by_state.setdefault(c.state, []).append(c.name)
            if by_state:
                system_parts.append("")
                system_parts.append("USER'S CONCEPT KNOWLEDGE (what they've studied before):")
                for state in ["solid", "learning", "rusty", "faded"]:
                    if state in by_state:
                        names = ", ".join(by_state[state][:10])
                        system_parts.append(f"- {state}: {names}")
                system_parts.append("Build on what they already know. Explain new concepts more carefully.")

        if learner.session_interest_summary:
            system_parts.append("")
            system_parts.append(f"CURRENT SESSION FOCUS:\n{learner.session_interest_summary}")

    if self_description:
        system_parts.append(f"\nUSER BACKGROUND:\n{self_description}")

    system_prompt = "\n".join(system_parts)

    user_parts = []

    if passage:
        user_parts.append(f"=== FULL PASSAGE ===\n{passage}")

    if selected_text:
        user_parts.append(f"=== HIGHLIGHTED TEXT (user is asking about this part) ===\n{selected_text}")

    if doc_chunks:
        context = "\n---\n".join(c.text for c in doc_chunks)
        user_parts.append(f"=== ADDITIONAL CONTEXT FROM DOCUMENT ===\n{context}")

    user_parts.append(f"=== QUESTION ===\n{question}")

    if similar_interactions:
        past = []
        for i in similar_interactions:
            entry = f"Q: {i.question}\nA: {i.answer[:200]}"
            past.append(entry)
        user_parts.append(
            "=== BACKGROUND: user's past questions (for reference only) ===\n"
            + "\n---\n".join(past)
        )

    user_prompt = "\n\n".join(user_parts)
    return system_prompt, user_prompt
