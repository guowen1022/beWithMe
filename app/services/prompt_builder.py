import re
from typing import Optional, List, NamedTuple, Tuple
from app.models.interaction import Interaction
from app.models.document import DocumentChunk
from app.user_profile.state import UserProfileState
from app.knowledge.models import ConceptNode
from app.knowledge.hlr import compute_mastery, mastery_to_state
from datetime import datetime


_TITLE_RE = re.compile(r"^\s*TITLE:\s*(.+?)\s*(?:\n+|$)", re.IGNORECASE)
_CONCEPTS_RE = re.compile(r"\n*\s*CONCEPTS:\s*[^\n]*\s*$", re.IGNORECASE)


def parse_title(answer: str) -> Tuple[Optional[str], str]:
    """Extract the leading TITLE: line emitted by the model.

    Returns `(title, body)`. If no TITLE line is found, title is None and
    body is the original answer untouched. The title is capped at 200 chars
    so it always fits the DB column.
    """
    m = _TITLE_RE.match(answer)
    if not m:
        return None, answer
    title = m.group(1).strip().rstrip(".!?")[:200]
    body = answer[m.end():]
    return title, body


def clean_answer_for_history(answer: str) -> str:
    """Strip TITLE: and CONCEPTS: metadata lines so historical assistant
    turns sent back to the LLM contain only the prose the user actually saw.
    """
    _, body = parse_title(answer)
    return _CONCEPTS_RE.sub("", body).strip()


def build_history_messages(prior_interactions: List[Interaction]) -> List[dict]:
    """Map a chronologically-ordered list of prior session interactions into
    Anthropic messages: alternating user (question, with selected text inline
    when present) and assistant (cleaned answer body) turns.
    """
    msgs: List[dict] = []
    for i in prior_interactions:
        user_text = i.question
        msgs.append({"role": "user", "content": user_text})
        msgs.append({"role": "assistant", "content": clean_answer_for_history(i.answer) or "(empty)"})
    return msgs


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
        "LENGTH AND STYLE (STRICT):",
        "- Be SHORT. Target 3-6 sentences total. Never write more than ~120 words unless the user explicitly asks for depth.",
        "- Use SHORT sentences. Break ideas across multiple short sentences instead of writing one long compound sentence. Aim for ~15 words per sentence, hard max ~25.",
        "- No preamble, no 'Great question', no restating the question, no closing summary. Go straight to the answer.",
        "- Prefer one direct answer followed by one or two sentences of why/how. Skip tangents.",
        "",
        "HOW TO ANSWER:",
        "1. The passage tells you WHAT the user is reading. Use it to understand the topic and context.",
        "2. IF the user message contains a HIGHLIGHTED TEXT block, that highlighted text is the PRIMARY SUBJECT of their question. Read it carefully before answering. Interpret the question in DIRECT relation to the highlighted text — pronouns and references like 'this', 'it', 'the first one', 'the second', 'the next' refer to entities in the highlighted text, NOT the broader passage. If the question is ambiguous, resolve the ambiguity by re-reading the highlighted text.",
        "3. Use your FULL KNOWLEDGE to answer — but in the shortest form that still answers the question well.",
        "4. If the question needs current/specific facts beyond your training data, say so honestly rather than guessing. State what you do know and where the user might find current numbers.",
        "5. The passage is the starting point, not the boundary. Draw on everything you know about the topic.",
        "6. This may be a multi-turn conversation. Earlier user/assistant turns in the messages array are PRIOR exchanges in the same reading session — treat them as live context the user can still see, and resolve follow-up references against them.",
        "7. If the user message includes a USER'S CONCEPT KNOWLEDGE block, build on what they already know and explain unfamiliar concepts more carefully.",
        "",
        "OUTPUT FORMAT (STRICT — these two metadata lines are parsed by the app):",
        "- The VERY FIRST line of your response must be: TITLE: <one-line summary of the question, max 60 chars, no trailing punctuation>",
        "- Then a blank line, then the answer body.",
        "- The VERY LAST line must be: CONCEPTS: concept1, concept2, concept3 — listing 1-5 domain knowledge concepts covered in your answer (textbook-level terms only, no generic words).",
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

    if doc_chunks:
        context = "\n---\n".join(c.text for c in doc_chunks)
        dynamic_parts.append(f"=== ADDITIONAL CONTEXT FROM DOCUMENT ===\n{context}")

    # Highlighted text + question live together at the very end of the
    # prompt so the model reads them as one unit. Pronouns in the question
    # should resolve against the highlighted text, not the broader passage.
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
