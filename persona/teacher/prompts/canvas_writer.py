"""Canvas-writer prompt — second pass of the voice-leads turn.

Receives the voice pass's transcript, the user's original question, and
(Phase 2) the full cached HTML of every note currently on canvas.
Emits ONE tool call:

  * `mount_template(note, ...)` — no note on canvas yet, or
    the existing one is on an unrelated topic;
  * `edit_note(block_id, ops=[...])` — an existing note is
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
    existing_notes: Optional[Dict[str, str]] = None,
    related_notes: Optional[List[dict]] = None,
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

    # Phase 2.5: inject the cached MARKDOWN source of every existing
    # note. Markdown is the source of truth; the client renders HTML.
    # The writer reads md and emits md ops back, keeping a clean diff
    # surface and a small tool-args payload.
    if existing_notes:
        for bid, source in existing_notes.items():
            dynamic_parts.append(
                f"=== CURRENT note BLOCK_ID={bid} (MARKDOWN) ===\n"
                f"{source.strip()}\n"
                "=== END ==="
            )

    # Related notes from storage that are NOT currently on canvas. When
    # a related slug clearly matches the topic, the writer should
    # RE-DISPLAY it (mount with no params hydrates the stored HTML from
    # disk) — that way the user sees their prior note again instead of an
    # empty canvas. Authoring a NEW note with `params.markdown` and the
    # same slug would silently overwrite the stored content; only do that
    # when intentionally rewriting from scratch.
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
                "=== RELATED STORED NOTES (not currently on canvas) ===\n"
                "(Semantic matches from the user's prior notes. If one "
                "clearly covers the topic the spoken answer just "
                "addressed: **RE-DISPLAY it** so the user sees it again. "
                "Call `mount_template(template='note', slug='<that-slug>')` "
                "with NO `params` — that hydrates the stored HTML from "
                "disk and re-mounts the note (no re-author, no overwrite). "
                "Only author a brand-new note (`params.markdown=…`) when "
                "the topic is genuinely different from every stored slug "
                "here.\n"
                "Slug rules when authoring NEW: the slug must pass the "
                "**standalone test** — it names a *thing* (entity or "
                "concept) that could carry its own first paragraph "
                "without referencing a parent topic. If it's just a "
                "facet of an existing topic, use `edit_note` on that "
                "parent slug instead. 2–3 words max. If your candidate "
                "slug would start with (or wholly contain) any stored "
                "slug below — e.g. candidate `steve-jobs-apple-comeback` "
                "vs stored `steve-jobs` — DO NOT create a new slug; "
                "re-display the stored one or use `edit_note` to extend "
                "it. Avoid polysemous bare tokens (`jobs` = employment "
                "vs surname; `apple` = fruit vs company; `mercury` = "
                "planet vs element vs person) — prefer the concept "
                "(`employment`, `careers`) over the colloquial token "
                "(`jobs`). Authoring with the SAME slug silently "
                "overwrites — avoid unless you intentionally want to "
                "rewrite from scratch.)\n"
                f"{joined}"
            )

    dynamic_parts.append(f"=== USER QUESTION ===\n{question}")

    dynamic_parts.append(
        "=== SPOKEN ANSWER (already delivered to the user as audio) ===\n"
        f"{voice_transcript.strip()}"
    )

    if existing_notes:
        dynamic_parts.append(
            "Decide: mount a new note, EDIT the existing one via "
            "edit_note (append / highlight / revise), or do nothing. "
            "Prefer edit when the topic continues; mount only when the "
            "topic is wholly different."
        )
    else:
        dynamic_parts.append(
            "Mount the note (or do nothing if the spoken answer is "
            "complete on its own). Emit only the tool call."
        )

    dynamic_user = "\n\n".join(dynamic_parts)

    return PromptParts(
        static_system=static_system,
        static_user_passage=static_user_passage,
        dynamic_user=dynamic_user,
    )


__all__ = ["build"]
