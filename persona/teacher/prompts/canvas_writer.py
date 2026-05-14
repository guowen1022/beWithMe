"""Canvas-writer prompt — second pass of the voice-leads turn.

Receives the voice pass's transcript, the user's original question, and
(Phase 2) the full cached HTML of every rich_card currently on canvas.
Emits ONE tool call:

  * `mount_template(rich_card, ...)` — no rich_card on canvas yet, or
    the existing one is on an unrelated topic;
  * `edit_rich_card(block_id, ops=[...])` — an existing rich_card is
    on-topic and should evolve in place (append / highlight / revise);
  * nothing — the spoken answer was self-contained and a card would
    just be noise.

Deliberately stripped: no teaching principle, no preferences, no mastery
context, no full passage. The writer's job is rendering the transcript,
not re-teaching.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from persona.teacher.prompts.canvas_renderer import format_canvas_state
from persona.teacher.prompts.parts import PromptParts
from persona.teacher.prompts.skills import load_skill


_CANVAS_SKILLS = (
    "workshop/canvas/grid",
    "workshop/canvas/lifecycle",
    "workshop/canvas/state_kinds",
    "workshop/canvas/layering",
)


def build(
    question: str,
    voice_transcript: str,
    canvas_state: object = None,
    existing_rich_cards: Optional[Dict[str, str]] = None,
) -> PromptParts:
    system_parts: List[str] = []

    for skill_name in _CANVAS_SKILLS:
        body = load_skill(skill_name)
        if body:
            system_parts.append(body)
            system_parts.append("")

    writer_skill = load_skill("teacher/canvas_writer")
    if writer_skill:
        system_parts.append(writer_skill)
        system_parts.append("")

    static_system = "\n".join(system_parts).rstrip() + "\n"

    static_user_passage = ""

    dynamic_parts: List[str] = []

    canvas_section = format_canvas_state(canvas_state)
    if canvas_section:
        dynamic_parts.append(canvas_section)

    # Phase 2: inject the FULL cached HTML of every existing rich_card so
    # the writer can pick between mount / edit / no-op with full context.
    # Without this the writer would only see a 200-char plaintext preview
    # via the canvas summary above and might overwrite real content.
    if existing_rich_cards:
        for bid, html in existing_rich_cards.items():
            dynamic_parts.append(
                f"=== CURRENT rich_card BLOCK_ID={bid} (HTML) ===\n"
                f"{html.strip()}\n"
                "=== END ==="
            )

    dynamic_parts.append(f"=== USER QUESTION ===\n{question}")

    dynamic_parts.append(
        "=== SPOKEN ANSWER (already delivered to the user as audio) ===\n"
        f"{voice_transcript.strip()}"
    )

    if existing_rich_cards:
        dynamic_parts.append(
            "Decide: mount a new rich_card, EDIT the existing one via "
            "edit_rich_card (append / highlight / revise), or do nothing. "
            "Prefer edit when the topic continues; mount only when the "
            "topic is wholly different."
        )
    else:
        dynamic_parts.append(
            "Mount the rich_card (or do nothing if the spoken answer is "
            "complete on its own). Emit only the tool call."
        )

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage=static_user_passage,
        dynamic_user=dynamic_user,
    )


__all__ = ["build"]
