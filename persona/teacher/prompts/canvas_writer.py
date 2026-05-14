"""Canvas-writer prompt — second pass of the voice-leads turn.

Receives the voice pass's transcript and the user's original question.
Mounts (at most) one `rich_card` that deepens the spoken answer with
structure / diagrams / named entities the ear can't hold.

Deliberately stripped: no teaching principle, no preferences, no mastery
context, no full passage. The writer's job is rendering the transcript,
not re-teaching. Keeping the prompt small also keeps the tool-call args
the dominant cost of the turn — and the writer only emits one mount.
"""
from __future__ import annotations

from typing import List

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

    dynamic_parts.append(f"=== USER QUESTION ===\n{question}")

    dynamic_parts.append(
        "=== SPOKEN ANSWER (already delivered to the user as audio) ===\n"
        f"{voice_transcript.strip()}"
    )

    dynamic_parts.append(
        "Now mount the rich_card (or do nothing if the spoken answer is "
        "complete on its own). Emit only the tool call."
    )

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage=static_user_passage,
        dynamic_user=dynamic_user,
    )


__all__ = ["build"]
