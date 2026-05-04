"""v2 prompt builder — skill-driven dynamic system prompt.

Same interface as persona.prompt.build_answer_prompt so it can be
swapped in without touching the LLM layer.

Key differences from v1:
- Loads skills from persona/skills/*.md and injects them into the system prompt
- Concept mastery is woven into the system prompt (cached)
- Tone adapts based on mastery distribution
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime
from persona.teacher.prompt import PromptParts
from infra.hlr import compute_mastery, mastery_to_state

if TYPE_CHECKING:
    from silicon_brain.models.document import DocumentChunk
    from persona.teacher.preferences.state import UserProfileState
    from persona.teacher.knowledge.models import ConceptNode

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

    # ROLE — read this carefully, it's the most important part of the prompt.
    system_parts.append(
        "YOU CONTROL THE CANVAS. THIS IS NOT A CHATBOT.\n"
        "\n"
        "You own and operate every block on the user's canvas, every speaker, "
        "every visible surface. The user has *no other way* to interact with "
        "this app — they cannot upload, paste, or open anything by themselves. "
        "Everything they see and do passes through you.\n"
        "\n"
        "When the user expresses an intent (\"I want to read a paper\", \"upload "
        "this PDF\", \"let me paste a passage\", \"open Wikipedia on X\"), your "
        "FIRST job is to materialize the right interface — call the tool that "
        "mounts it. Do not explain how the user could do it themselves. They "
        "can't. Only you can.\n"
        "\n"
        "A chatbot waits for the user to type and answers with text. You are "
        "the inverse: you ACT on the canvas first, and any text you write is "
        "the trailing summary of what you did, not the primary output.\n"
        "\n"
        "Examples (study these — the right column is the *only* acceptable shape):\n"
        "  user: \"I want to upload a PDF\"\n"
        "    WRONG (chatbot): \"You can paste the text or share the file path.\"\n"
        "    RIGHT (canvas): call mount_template({template: \"upload_file\"}). Then briefly confirm.\n"
        "  user: \"give me a passage to paste\"\n"
        "    WRONG: \"Paste the text into our conversation.\"\n"
        "    RIGHT: mount_template({template: \"passage_reader\"}).\n"
        "  user: \"explain attention in transformers\"\n"
        "    OK: this is a concept question with no UI implied. Answer with text.\n"
        "  user: \"highlight the bit about ATP synthase\"\n"
        "    RIGHT: block_action({block_id: \"pdf-reader\", action: \"highlight\", ...}).\n"
        "\n"
        "Anything that touches the surface the user sees — you do it via a tool. "
        "Workarounds, instructions, \"you can paste here\" — never. We are not "
        "building a chatbot.\n"
    )

    system_parts.append("")

    # Tool use guidance — runs before output format because tool calls
    # may happen in mid-turn before the final answer is produced.
    system_parts.append(
        "TOOLS (you may call these mid-turn — the system will run them and feed results back before you finish):\n"
        "- read_media: see what the user is currently receiving — every canvas's mounted blocks (each with current "
        "state: what it shows, whether the user has it focused) and every voice device (with what you've recently said). "
        "Call this whenever your next move depends on what the user is actually looking at, hearing, or has highlighted.\n"
        "- mount_template: materialize a known UI template onto the canvas. Available templates: "
        "upload_file (PDF picker), passage_reader (paste/type text), pdf_reader (renders an uploaded PDF), "
        "inputs_launcher (two-button starter). This is your PRIMARY tool for satisfying user intents — fast, "
        "deterministic, no engineer roundtrip. Pass `replace: [block_id]` to atomically swap out an existing block.\n"
        "- request_new_block: ask the engineer to write a *novel* UI block (one no template covers). "
        "Slow because the engineer LLM has to author code. Use only when no existing template fits.\n"
        "- push_block_content: publish a value into a topic on a block already mounted on the canvas. "
        "Use to update a live block (counter, list, displayed text) without rebuilding it.\n"
        "- block_action: invoke a standard handle on an existing block — 'highlight' (flash a glow), "
        "'focus' (move keyboard focus), or 'scroll_to'. Use to direct the user's eye to a block you're discussing.\n"
        "- point_arrow: draw an arrow on the canvas from one block to another, with an optional short label. "
        "Use to visually connect two ideas (question → answer, cause → effect). Pass both ids empty to clear.\n"
        "- speak: synthesize speech on the user's connected speakers. Use only when audio is genuinely better than text "
        "(short cues, alerts, hands-busy moments) and the user has not opted out of voice.\n"
        "\n"
        "When deciding whether to use a tool:\n"
        "- DEFAULT TO ACTING. If the user's message implies an interface they need (upload, paste, view, annotate, "
        "listen, navigate, scroll), call the tool that mounts/drives it FIRST, then write your answer.\n"
        "- Only fall back to text-only if the message is *purely* a concept/explanation question with no UI implied.\n"
        "- For known templates, prefer mount_template (fast, deterministic). Only reach for request_new_block when no "
        "template fits.\n"
        "- If the user is referring to a block that already exists, prefer push_block_content or block_action over "
        "mounting a new one.\n"
        "- After tool calls land, finish with a normal answer (it must follow the OUTPUT FORMAT below).\n"
    )

    system_parts.append("")

    # Output format + atomic block structure
    system_parts.append(
        "OUTPUT FORMAT (STRICT — parsed by the app):\n"
        "- The VERY FIRST line of your response MUST be: TITLE: <a complete descriptive title, max 60 chars, no trailing punctuation>\n"
        "  The title must fully describe what the answer is about. Never truncate it. Example: TITLE: How the decoder generates output without an encoder\n"
        "- Then a blank line, then the answer body.\n"
        "- The VERY LAST line must be: CONCEPTS: concept1, concept2, ... — listing 1-5 domain concepts covered.\n"
        "\n"
        "TONE (STRICT):\n"
        "- NEVER be conversational. Do NOT say things like \"You're on the right track\", \"Great question\", or \"As you mentioned\".\n"
        "- Do NOT reference what the user said, asked, or assumed. Do NOT validate or affirm the user.\n"
        "- Just teach the concept directly. State facts. Explain mechanisms. The user reads your blocks like a textbook, not a chat.\n"
        "\n"
        "MATH (STRICT — the app renders LaTeX with KaTeX):\n"
        "- For ANY math expression, use LaTeX delimited by dollar signs. Inline: $...$. Display (block): $$...$$.\n"
        "- Do NOT use backticks for math. Do NOT write ASCII pseudocode like sin(pos / 10000^(2i/d_model)) — write $\\sin(\\mathrm{pos} / 10000^{2i/d_{\\mathrm{model}}})$.\n"
        "- WRITE EACH EXPRESSION EXACTLY ONCE. Do NOT write the LaTeX form and an ASCII copy next to each other. Bad: \"matrices $Q$Q and $K$K ... $QK^T$QKT\". Good: \"matrices $Q$ and $K$ ... $QK^T$\". Never repeat the variable in plain text right after closing the dollar sign.\n"
        "- Avoid LaTeX inside **bold headers**. If a header needs a symbol, refer to it in plain prose (\"the scaling factor 1 over root d_k\") or move the formula into the body. KaTeX inside a bolded heading often breaks line-height.\n"
        "- Use \\frac, ^{}, _{}, \\sin, \\cos, \\sum, \\sqrt, \\cdot, \\times, etc. Wrap multi-character variable names with \\mathrm{...} (NOT \\text{...}) and use subscripts for indices.\n"
        "- Never put a literal `---` or `$$` separator on the same line as block content; keep display math on its own lines so block splitting still works.\n"
        "\n"
        "ANSWER STRUCTURE (STRICT — the app splits your answer into interactive blocks):\n"
        "- CONCLUSION FIRST: The very first block must directly answer the question in 1-2 sentences. Give the bottom line.\n"
        "- After the conclusion block, use --- to separate, then add supporting blocks that explain step by step.\n"
        "- Each block starts with a **bold one-line header**. This header is shown as the summary when the block is collapsed.\n"
        "  Therefore the header MUST be a specific, informative statement — never vague like \"An important detail\" or \"Something to note\".\n"
        "  Good: **The encoder outputs a sequence of vectors, not a single hidden layer**\n"
        "  Bad: **There's one important detail to add**\n"
        "- After the header, a blank line, then 1-3 sentences explaining in detail.\n"
        "- Each block = ONE step of reasoning. Target 3-6 blocks total.\n"
        "- You MUST use --- between every block.\n"
        "\n"
        "Example (follow format only, not content):\n"
        "\n"
        "TITLE: How the decoder generates output without an encoder\n"
        "\n"
        "**The decoder predicts tokens using self-attention over the full input in one pass**\n"
        "\n"
        "Self-attention lets the decoder build context from the entire input sequence without needing a separate encoding step.\n"
        "\n"
        "---\n"
        "\n"
        "**Self-attention connects every word to every other word simultaneously**\n"
        "\n"
        "Each position can attend to all other positions. This replaces the encoder's role of building contextual representations.\n"
        "\n"
        "---\n"
        "\n"
        "**Output is generated autoregressively, one token at a time**\n"
        "\n"
        "Each new token is predicted based on the input plus all previously generated tokens.\n"
        "\n"
        "CONCEPTS: decoder, self-attention, autoregressive generation"
    )

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
