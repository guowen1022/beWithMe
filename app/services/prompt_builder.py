from typing import Optional, List, Tuple
from app.models.interaction import Interaction
from app.models.document import DocumentChunk


def build_answer_prompt(
    passage: Optional[str],
    selected_text: Optional[str],
    question: str,
    self_description: str,
    similar_interactions: List[Interaction],
    doc_chunks: List[DocumentChunk],
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
    if self_description:
        system_parts.append(f"\nUSER BACKGROUND:\n{self_description}")

    system_prompt = "\n".join(system_parts)

    user_parts = []

    # Full passage for context
    if passage:
        user_parts.append(f"=== FULL PASSAGE ===\n{passage}")

    # Highlighted selection
    if selected_text:
        user_parts.append(f"=== HIGHLIGHTED TEXT (user is asking about this part) ===\n{selected_text}")

    if doc_chunks:
        context = "\n---\n".join(c.text for c in doc_chunks)
        user_parts.append(f"=== ADDITIONAL CONTEXT FROM DOCUMENT ===\n{context}")

    # Question
    user_parts.append(f"=== QUESTION ===\n{question}")

    # Past interactions are LAST and clearly secondary
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
