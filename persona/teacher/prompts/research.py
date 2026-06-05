"""Research-mode prompt — builds the system + dynamic-user payload for
Lane R (the long-horizon investigator turn spawned by `start_research`).

Differs from reflect.py:
  - Loads `research_policy.md` instead of `reflect_policy.md` +
    `respond_to_speech.md`. The two are mutually exclusive: reflect's
    "silence-by-default" gate would directly contradict research's
    "plan and execute" instruction.
  - Carries the goal text (the user's question, restated by Lane A) as
    the dynamic user message, plus the live canvas state. No perception
    events — Lane R fires from a tool call, not from a perception event.
"""
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from persona.teacher.prompts import preferences_block
from persona.teacher.prompts.canvas_renderer import format_canvas_state
from persona.teacher.prompts.learner_state import summarise_mastery
from persona.teacher.prompts.parts import PromptParts
from persona.teacher.prompts.skills import load_skill

if TYPE_CHECKING:
    from persona.teacher.preferences.state import UserProfileState
    from persona.teacher.knowledge.models import ConceptNode


_CANVAS_SKILLS = (
    "workshop/canvas/grid",
    "workshop/canvas/lifecycle",
    "workshop/canvas/state_kinds",
    "workshop/canvas/layering",
    "workshop/canvas/tool_verbs",
)


def build(
    goal: str,
    canvas_state: object = None,
    user_profile: Optional["UserProfileState"] = None,
    concept_nodes: Optional[List["ConceptNode"]] = None,
    self_description: str = "",
    talk_preference: dict | None = None,
    *,
    host: Optional[str] = None,
    per_host_note: Optional[str] = None,
) -> PromptParts:
    """Build the research-mode prompt.

    `goal` is the question the user asked (restated by Lane A's
    start_research call). `canvas_state` is the live canvas snapshot —
    the investigator needs to know what's on screen to ground its
    investigation (e.g. a `web_view` block already showing the page).

    `host` + `per_host_note`: optional pre-fetched navigation cheatsheet
    for the URL host the research targets. When both are present, a
    "KNOWN NOTES FOR THIS SITE" section is prepended to dynamic_user so
    the agent doesn't re-discover navigation patterns it (or any prior
    user) already learned about this site. See
    `workshop/research/per_host_skills.py`.
    """
    # ---- STATIC SYSTEM ---------------------------------------------------
    system_parts: List[str] = []

    teaching_principle = load_skill("teacher/teaching_principle")
    if teaching_principle:
        system_parts.append(teaching_principle)
        system_parts.append("")

    canvas_persona = load_skill("teacher/canvas_persona")
    if canvas_persona:
        system_parts.append(canvas_persona)
        system_parts.append("")

    for skill_name in _CANVAS_SKILLS:
        body = load_skill(skill_name)
        if body:
            system_parts.append(body)
            system_parts.append("")

    research_policy = load_skill("teacher/research_policy")
    if research_policy:
        system_parts.append(research_policy)
        system_parts.append("")

    # Event-stream discipline — when to call `stream_emit` (default: silence).
    stream_emission = load_skill("teacher/stream_emission")
    if stream_emission:
        system_parts.append(stream_emission)
        system_parts.append("")

    system_parts.extend(preferences_block.render(user_profile, self_description))
    system_parts.extend(preferences_block.render_talk_preference(talk_preference))

    mastery = summarise_mastery(concept_nodes)
    if mastery:
        system_parts.append("")
        system_parts.append(mastery)

    static_system = "\n".join(system_parts)

    # ---- DYNAMIC USER ---------------------------------------------------
    dynamic_parts: List[str] = []

    canvas_section = format_canvas_state(canvas_state)
    if canvas_section:
        dynamic_parts.append(canvas_section)

    # Per-host navigation cheatsheet. Sits ABOVE the goal so the agent
    # reads it before forming its plan and can use it to skip steps it
    # would otherwise burn iterations discovering.
    if host and per_host_note and per_host_note.strip():
        dynamic_parts.append(
            f"=== KNOWN NOTES FOR THIS SITE ({host}) ===\n"
            f"{per_host_note.strip()}\n\n"
            "These are navigation tips accumulated from prior research on "
            "this site (across all users). Use them to skip rediscovery — "
            "but verify before relying on a specific selector if the site "
            "might have changed."
        )

    dynamic_parts.append(
        "=== RESEARCH GOAL ===\n"
        f"{goal.strip()}\n\n"
        "Your first tool call must be `research_plan` with 3–7 concrete "
        "steps. After each step, call `research_note(step_index, finding)`. "
        "End with exactly one `speak` carrying your synthesis."
    )

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage="",
        dynamic_user=dynamic_user,
    )


__all__ = ["build"]
