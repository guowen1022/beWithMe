"""v2 prompt builder — skill-driven dynamic system prompt.

Same interface as app.teacher.prompt.build_answer_prompt so it can be
swapped in without touching the LLM layer.

Key differences from v1:
- Loads skills from app/teacher/skills/*.md and injects them into the system prompt
- Concept mastery is woven into the system prompt (cached)
- Tone adapts based on mastery distribution
"""

from pathlib import Path
from typing import Optional, List
from datetime import datetime
from app.teacher.prompt import PromptParts
from app.silicon_brain.models.document import DocumentChunk
from app.silicon_brain.user_profile.state import UserProfileState
from app.silicon_brain.knowledge.models import ConceptNode
from app.silicon_brain.knowledge.hlr import compute_mastery, mastery_to_state

_SKILLS_DIR = Path(__file__).parent / "skills"


def load_skill(name: str) -> str:
    """Load a skill markdown file by name (without extension).

    Returns the file contents, or empty string if not found.
    """
    path = _SKILLS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text().strip()
    return ""


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
    """Build the answer prompt v2 — loads skills from markdown files."""

    # ---- Load skills --------------------------------------------------------
    teaching_principle = load_skill("teaching_principle")

    # ---- Analyse learner state to set tone --------------------------------
    mastery_summary = ""
    beginner_mode = True
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
    system_parts = [
        f"You are a helpful and patient reading assistant. Please read the teaching principles (app/teacher/skills/teaching_principle.md).",
    ]

    # Inject teaching principles from skill file
    if teaching_principle:
        system_parts.append("")
        system_parts.append(teaching_principle)

    system_parts.append("")

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
