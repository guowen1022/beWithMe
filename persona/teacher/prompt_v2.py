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


_GRAPH_BLOCK_PREFIX = "interactive-graph"


def _diagram_name_from_block_id(block_id: str) -> str:
    """`interactive-graph` → `main`; `interactive-graph-steps` → `steps`."""
    if block_id == _GRAPH_BLOCK_PREFIX:
        return "main"
    if block_id.startswith(_GRAPH_BLOCK_PREFIX + "-"):
        return block_id[len(_GRAPH_BLOCK_PREFIX) + 1:]
    return block_id


def _format_canvas_state(perc: object) -> str:
    """Render the user's current canvas + voice state as a terse, intent-vocab
    section the teacher can read without thinking about block ids.

    `perc` is a MediaPerception (avoiding the import here to keep this
    builder framework-light). Returns empty string when nothing is up.
    """
    if perc is None:
        return ""
    canvases = getattr(perc, "canvases", []) or []
    voices = getattr(perc, "voices", []) or []

    lines: list[str] = []
    seen_block_ids: set[str] = set()  # collapse duplicates across devices
    for canvas in canvases:
        if not getattr(canvas, "online", False):
            continue
        for block in getattr(canvas, "blocks", []) or []:
            bid = block.id
            if bid in seen_block_ids:
                continue
            seen_block_ids.add(bid)
            line = _format_block_line(block)
            if line:
                lines.append(line)

    voice_lines: list[str] = []
    for voice in voices:
        if not getattr(voice, "online", False):
            continue
        utts = getattr(voice, "recent_utterances", []) or []
        if not utts:
            continue
        last = utts[-1]
        text = (getattr(last, "text", "") or "").strip().replace("\n", " ")
        if len(text) > 70:
            text = text[:67] + "…"
        voice_lines.append(f'- voice: last said "{text}"')

    if not lines and not voice_lines:
        return ""

    parts = ["=== CURRENTLY ON CANVAS ==="]
    parts.extend(lines)
    parts.extend(voice_lines)
    return "\n".join(parts)


def _format_block_line(block) -> str:
    """One line per surface, in the teacher's vocabulary (never expose block id)."""
    state = getattr(block, "state", None)
    title = getattr(block, "title", None)
    bid = block.id
    age = getattr(block, "last_updated_s_ago", None)

    # Diagram surface (interactive-graph instances): describe by name + kind.
    if bid == _GRAPH_BLOCK_PREFIX or bid.startswith(_GRAPH_BLOCK_PREFIX + "-"):
        name = _diagram_name_from_block_id(bid)
        if state is not None and state.kind == "graph":
            extra = state.extra or {}
            kind = extra.get("mermaid_kind") or "diagram"
            n_nodes = len(extra.get("node_ids") or [])
            sel = extra.get("selected_node_id")
            bits = [f'- diagram "{name}": {kind}, {n_nodes} nodes']
            if sel:
                bits.append(f'(selected: "{sel}")')
            tail = _format_focus_tail(state, age)
            if tail:
                bits.append(tail)
            return " ".join(bits)
        return f'- diagram "{name}" (empty)'

    # Main reading area (Reader.tsx's PDF / passage / browser surface).
    # Suppress the empty placeholder state — no value to the teacher.
    if bid == "main-reader":
        if state is None or state.kind in ("snapshot", None):
            return ""

    # Other surfaces: dispatch on state.kind.
    head = None
    if state is not None:
        kind = state.kind
        content = (state.content or "").strip().replace("\n", " ")
        if len(content) > 70:
            content = content[:67] + "…"
        if kind == "pdf":
            head = f'- PDF reader: "{title or content or "open"}"'
        elif kind == "passage":
            label = title or content or "(empty)"
            head = f'- text panel: "{label}"'
        elif kind == "browser":
            head = f"- browser: {content or '(loading)'}"
        elif kind == "snapshot":
            label = title or content or bid
            head = f'- panel: "{label}"'
        else:
            head = f"- {kind} panel" + (f': "{content}"' if content else "")
    else:
        head = f'- {title or bid} (no state yet)'

    tail = _format_focus_tail(state, age) if state is not None else ""
    return head + (f" {tail}" if tail else "")


def _format_focus_tail(state, age) -> str:
    """e.g. '(user is here, 12s ago)' / '(idle)'."""
    if state is None:
        return ""
    pieces = []
    focus = state.focus
    if focus == "active":
        pieces.append("user is here")
    elif focus == "background":
        pieces.append("idle")
    if age is not None:
        if age < 60:
            pieces.append(f"{int(age)}s ago")
        elif age < 3600:
            pieces.append(f"{int(age // 60)}m ago")
    if not pieces:
        return ""
    return "(" + ", ".join(pieces) + ")"


def build_answer_prompt(
    passage: Optional[str],
    selected_text: Optional[str],
    question: str,
    self_description: str,
    doc_chunks: List[DocumentChunk],
    user_profile: Optional[UserProfileState] = None,
    concept_nodes: Optional[List[ConceptNode]] = None,
    graph_context: str = "",
    canvas_state: object = None,
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
        "  user: \"show me the flow: step 1 eat well, step 2 sleep well\"\n"
        "    WRONG: request_new_block({description: \"a flow with two steps\"}).\n"
        "           (request_new_block authors fresh JavaScript per call. Diagrams are CONTENT,\n"
        "            not code; per-step JS does not belong in the user's workspace.)\n"
        "    RIGHT: interactive_graph({name: \"steps\",\n"
        "             mermaid: \"flowchart LR\\n  A[EAT WELL] --> B[SLEEP WELL]\"}).\n"
        "  user: \"now add 'exercise daily' as a third step\"\n"
        "    RIGHT: interactive_graph({name: \"steps\",\n"
        "             mermaid: \"flowchart LR\\n  A[EAT WELL] --> B[SLEEP WELL] --> C[EXERCISE DAILY]\"}).\n"
        "           (Same `name` — replaces the same diagram in place.)\n"
        "  user: \"also draw the TLS handshake separately\"\n"
        "    RIGHT: interactive_graph({name: \"tls\",\n"
        "             mermaid: \"sequenceDiagram\\n  Client->>Server: ClientHello\\n  ...\"}).\n"
        "           (Different `name` — second diagram appears alongside the first.)\n"
        "  user: \"draw the class hierarchy of User and Admin\"\n"
        "    RIGHT: interactive_graph({name: \"users\",\n"
        "             mermaid: \"classDiagram\\n  Admin --|> User\\n  class User { +String name }\"}).\n"
        "  user: \"chart Q1 sales as bars\"\n"
        "    RIGHT: interactive_graph({name: \"q1-sales\",\n"
        "             mermaid: \"xychart-beta\\n  title \\\"Q1 sales\\\"\\n  x-axis [Jan, Feb, Mar]\\n  bar [10, 25, 40]\"}).\n"
        "\n"
        "Diagrams are EPHEMERAL — they appear, illustrate, and disappear when the user reloads.\n"
        "Don't worry about saving them; that's the right behavior. Persistent saving is a separate,\n"
        "future feature the user opts in to.\n"
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
        "\n"
        "**You think in WHAT TO SHOW THE USER** — a diagram, a passage, a PDF, a sound. The system maps your "
        "intent to surfaces. The CURRENTLY ON CANVAS section above tells you what's already up, in the same "
        "vocabulary. You don't address surfaces by internal id; you address them by their human name (e.g. "
        "diagram \"steps\", text panel \"Glycolysis\").\n"
        "\n"
        "- read_media: see what's on every canvas / voice the user is currently using, with the latest self-reported "
        "state of each surface. The CURRENTLY ON CANVAS section above is a digest — call this for the full picture "
        "when you need block-level details.\n"
        "- mount_template: display a known reading surface — upload_file (PDF picker), passage_reader "
        "(paste/type text), pdf_reader (rendered PDF). Use when the user wants to upload, paste, or read.\n"
        "- interactive_graph: draw or update a diagram. Each diagram has a `name` you choose (e.g. \"steps\", "
        "\"protocol\"); same name = update existing, different name = add a second diagram alongside. Mermaid "
        "syntax — flowcharts, sequence diagrams, classes (UML), mindmaps, charts (bar/line/pie), gantt, sankey, "
        "timelines. Use this for ANY relational visual: \"step 1 → step 2\", \"class A inherits B\", \"compare "
        "options as a tree\". Diagrams are EPHEMERAL — they appear, illustrate, disappear on reload.\n"
        "- request_new_block: ONLY for novel *interactive widgets* — sliders, custom inputs, simulations. "
        "DO NOT use for diagrams (those go to interactive_graph). DO NOT use to display text or a passage "
        "(those go to mount_template). Slow because the engineer LLM has to write JavaScript.\n"
        "- push_block_content: send a value to a topic an existing surface listens on. Use to drive live "
        "data (counter, list, text update) into something already up — no remount.\n"
        "- block_action: draw the user's attention to an existing surface — 'highlight' (flash glow), "
        "'focus' (keyboard focus), 'scroll_to'.\n"
        "- point_arrow: draw an arrow between two surfaces with an optional label, to visually link two "
        "ideas. Pass both ids empty to clear.\n"
        "- speak: synthesize speech on the user's connected speakers. Use only when audio is genuinely "
        "better than text (short cues, alerts, hands-busy moments) and the user hasn't opted out.\n"
        "\n"
        "When deciding whether to use a tool:\n"
        "- DEFAULT TO ACTING. If the user's message implies an interface they need (upload, paste, view, annotate, "
        "listen, navigate, scroll), call the tool that mounts/drives it FIRST, then write your answer.\n"
        "- Only fall back to text-only if the message is *purely* a concept/explanation question with no UI implied.\n"
        "- For known templates, prefer mount_template (fast, deterministic). For flows, sequences, comparisons, "
        "hierarchies, charts, or any structural diagram — reach for interactive_graph (also fast, deterministic). "
        "Only fall back to request_new_block when neither fits.\n"
        "- If the user is referring to a surface that's already up (check CURRENTLY ON CANVAS), update it in "
        "place — push_block_content for new content, block_action to draw attention. Don't mount a duplicate.\n"
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

    canvas_section = _format_canvas_state(canvas_state)
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
