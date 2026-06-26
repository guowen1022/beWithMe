"""Reflect-scenario prompt — perception event woke the teacher.

Used by the trigger pipeline for ALL three perception event types
(`BlockCompletedEvent`, `BlockChangeEvent`, `VoiceEvent`). The events
list carries the type so the LLM can weight a `completed` event more
heavily than a `changed` one.

Drops the answer-format skill — reflect turns are not user-visible
answers; their text appears only in the developer debug panel as a
"thinking" note. Most reflect turns should produce zero tool calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, List, Optional, TYPE_CHECKING

from persona.teacher.prompts import preferences_block
from persona.teacher.prompts.canvas_renderer import format_canvas_state
from persona.teacher.prompts.learner_state import summarise_mastery
from persona.teacher.prompts.parts import PromptParts
from persona.teacher.prompts.skills import load_skill

if TYPE_CHECKING:
    from persona.teacher.preferences.state import UserProfileState
    from persona.teacher.knowledge.models import ConceptNode
    from infra.perception.contracts import UserUtterance


_CANVAS_SKILLS = (
    "workshop/canvas/grid",
    "workshop/canvas/lifecycle",
    "workshop/canvas/state_kinds",
    "workshop/canvas/layering",
    "workshop/canvas/tool_verbs",
)


@dataclass(frozen=True)
class PerceptionEventSummary:
    """A perception event normalised for prompt rendering. The trigger
    pipeline builds these from `BlockCompletedEvent` / `BlockChangeEvent`
    / `VoiceEvent` so the prompt module doesn't depend on the cache
    contracts directly."""
    event_type: str            # "completed" | "changed" | "voice"
    block_id: Optional[str]    # None for voice
    state_kind: Optional[str]  # state.kind for block events
    content: Optional[str]     # state.content excerpt for blocks; text for voice
    extra: Optional[dict] = None


def _format_events(events: Iterable[PerceptionEventSummary]) -> str:
    lines: List[str] = []
    for e in events:
        bits = [f"- [{e.event_type}]"]
        if e.block_id:
            bits.append(f"block_id={e.block_id}")
        if e.state_kind:
            bits.append(f"kind={e.state_kind}")
        if e.content:
            content = e.content.strip().replace("\n", " ")
            if len(content) > 80:
                content = content[:77] + "…"
            bits.append(f"content={content!r}")
        if e.extra:
            try:
                bits.append(f"extra={json.dumps(e.extra, default=str)}")
            except Exception:
                bits.append(f"extra={e.extra!r}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _format_recent_user_speech(
    utterances: Iterable["UserUtterance"],
) -> str:
    """One line per utterance, oldest first. Compact and prompt-cheap."""
    lines: List[str] = []
    for u in utterances:
        ts = u.captured_at.strftime("%H:%M:%S") if u.captured_at else "??:??:??"
        text = (u.text or "").strip().replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "…"
        lang = f" [{u.language}]" if u.language else ""
        lines.append(f"- {ts}{lang} {text!r}")
    return "\n".join(lines)


def build(
    events: List[PerceptionEventSummary],
    canvas_state: object = None,
    user_profile: Optional["UserProfileState"] = None,
    concept_nodes: Optional[List["ConceptNode"]] = None,
    self_description: str = "",
    talk_preference: dict | None = None,
    recent_user_speech: Optional[List["UserUtterance"]] = None,
    recent_notices: Optional[List[str]] = None,
    related_notes: Optional[List[dict]] = None,
    lead_pass: bool = False,
) -> PromptParts:
    """Build the reflect-scenario prompt.

    `events` is the (already coalesced) batch of perception events that
    triggered this turn. The reflect_policy.md skill tells the LLM how
    to read them: act on deterministic next steps, otherwise stay
    silent.

    When `lead_pass=True`, the prompt drops every skill that mentions
    tools (canvas_persona, canvas skills, respond_to_speech) and loads
    `lead_brief.md` in their place. The Lane A turn becomes the fast
    lead-pass spoken answer; deeper passes handle visuals/tools afterward.
    """
    # ---- STATIC SYSTEM ---------------------------------------------------
    system_parts: List[str] = []

    teaching_principle = load_skill("teacher/teaching_principle")
    if teaching_principle:
        system_parts.append(teaching_principle)
        system_parts.append("")

    if lead_pass:
        # Lead pass: no teaching tools on this pass. Load only the brevity
        # rules plus the reflect policy (which governs "stay silent vs. reply"
        # — still relevant). Skip canvas skills entirely; the deeper passes
        # own canvas.
        lead_brief = load_skill("teacher/lead_brief")
        if lead_brief:
            system_parts.append(lead_brief)
            system_parts.append("")

        reflect_policy = load_skill("teacher/reflect_policy")
        if reflect_policy:
            system_parts.append(reflect_policy)
            system_parts.append("")
    else:
        canvas_persona = load_skill("teacher/canvas_persona")
        if canvas_persona:
            system_parts.append(canvas_persona)
            system_parts.append("")

        for skill_name in _CANVAS_SKILLS:
            body = load_skill(skill_name)
            if body:
                system_parts.append(body)
                system_parts.append("")

        reflect_policy = load_skill("teacher/reflect_policy")
        if reflect_policy:
            system_parts.append(reflect_policy)
            system_parts.append("")

        respond_to_speech = load_skill("teacher/respond_to_speech")
        if respond_to_speech:
            system_parts.append(respond_to_speech)
            system_parts.append("")

    # Event-stream discipline — applies to both lead-pass + normal
    # reflect paths since both surface `stream_emit` on the background lane.
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
    # Reflect turns have no passage and no question. The user message
    # carries the canvas snapshot + the events that fired.
    dynamic_parts: List[str] = []

    canvas_section = format_canvas_state(canvas_state)
    if canvas_section:
        dynamic_parts.append(canvas_section)

    if recent_user_speech:
        speech_section = _format_recent_user_speech(recent_user_speech)
        if speech_section:
            dynamic_parts.append(
                "=== RECENT SPOKEN UTTERANCES (in-memory, this session) ===\n"
                f"{speech_section}"
            )

    if recent_notices:
        notices_lines = "\n".join(f"- {n}" for n in recent_notices)
        dynamic_parts.append(
            "=== RECENT BACKGROUND ACTIONS ===\n"
            "(Lane B finished these tasks since your last reply. Surface them "
            "naturally only if relevant to the user's last utterance.)\n"
            f"{notices_lines}"
        )

    if related_notes:
        chunks: List[str] = []
        for n in related_notes:
            slug = n.get("slug", "")
            score = n.get("score", 0.0)
            text = (n.get("text") or "").strip()
            if not slug or not text:
                continue
            if len(text) > 600:
                text = text[:597] + "…"
            chunks.append(f"[slug={slug}, similarity={score:.2f}]\n{text}")
        if chunks:
            joined = "\n---\n".join(chunks)
            dynamic_parts.append(
                "=== RELATED NOTES (from your prior teaching with this user) ===\n"
                "(Retrieved by semantic similarity to the user's recent speech. "
                "These notes may or may not be currently visible on canvas — "
                "they live in storage. Build on them when relevant: reference "
                "what you've already taught, cite the slug, or let a writer "
                "pass extend the matching note via `edit_note` instead of "
                "mounting a duplicate.)\n"
                f"{joined}"
            )

    events_section = _format_events(events)
    if events_section:
        dynamic_parts.append(f"=== PERCEPTION UPDATES ===\n{events_section}")
    else:
        # Defensive: trigger fired with no events somehow. Tell the LLM
        # so it doesn't hallucinate a justification.
        dynamic_parts.append("=== PERCEPTION UPDATES ===\n(no events in this batch)")

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage="",
        dynamic_user=dynamic_user,
    )


__all__ = ["build", "PerceptionEventSummary"]
