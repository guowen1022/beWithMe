"""Research-mode context assembly — minimal pipeline.

Lane R doesn't need RAG, document chunks, or perception events. It
needs the user's profile, preferences, and the live canvas state so
the investigator can ground its plan in what's already on screen
(e.g. a `web_view` block displaying the page the user is asking about).
"""
from __future__ import annotations

import uuid
from typing import Optional

from persona.teacher.contexts.answer import TeacherContext
from persona.teacher.preferences import get_user_profile
from persona.teacher.knowledge import get_concepts
from persona.teacher.prompts.research import build as build_research_prompt
from infra.silicon_brain_client import SiliconBrainClient
from workshop.canvas.tools.read_media import read_media
from workshop.research import per_host_skills
from workshop.research.recipes import host_from_url

from infra.db import async_session


async def assemble(
    user_id: uuid.UUID,
    goal: str,
    *,
    goal_url: Optional[str] = None,
) -> TeacherContext:
    """Build the research-scenario context for `goal`.

    `goal_url` (optional) is the URL the research targets — provided by
    the LLM via `start_research(page_url=...)` or auto-detected from
    canvas state. When present, we look up a per-host navigation note
    and prepend it to the prompt so the agent inherits any prior
    learnings about this site.
    """
    canvas_state = None
    try:
        canvas_state = await read_media(user_id)
    except Exception as e:
        print(f"[teacher.research] read_media error: {e}", flush=True)

    user_profile = None
    concept_nodes: list = []
    try:
        async with async_session() as db:
            user_profile = await get_user_profile(db, user_id, session_id=None)
            concept_nodes = await get_concepts(db, user_id, limit=30)
    except Exception as e:
        print(f"[teacher.research] db read error: {e}", flush=True)

    self_description = ""
    talk_preference: dict | None = None
    client = SiliconBrainClient()
    try:
        try:
            profile = await client.get_profile(user_id)
            self_description = profile.self_description if profile else ""
        except Exception as e:
            print(f"[teacher.research] get_profile error: {e}", flush=True)
        try:
            talk_preference = await client.get_talk_preference(user_id)
        except Exception as e:
            print(f"[teacher.research] get_talk_preference error: {e}", flush=True)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    # Per-host navigation note — global tier, shared across all users.
    # Fetched once, injected into the prompt, mark_used bumped. Failures
    # silently degrade to "no note" — the note is enrichment, not
    # required for research to work.
    host: Optional[str] = host_from_url(goal_url) if goal_url else None
    per_host_note: Optional[str] = None
    if host:
        try:
            skill = await per_host_skills.get(host)
            if skill is not None and skill.note.strip():
                per_host_note = skill.note
                # Bump use_count + updated_at on disk. Cheap, awaited so
                # the count is correct by the time the next caller runs.
                await per_host_skills.mark_used(host)
                print(
                    f"[per_host_skills] injected {host} note into prompt "
                    f"({len(per_host_note)} chars)",
                    flush=True,
                )
        except Exception as e:
            print(f"[teacher.research] per_host_skills lookup error: {e}", flush=True)

    parts = build_research_prompt(
        goal=goal,
        canvas_state=canvas_state,
        user_profile=user_profile,
        concept_nodes=concept_nodes,
        self_description=self_description,
        talk_preference=talk_preference,
        host=host,
        per_host_note=per_host_note,
    )

    return TeacherContext(parts=parts, prior_messages=[])


__all__ = ["assemble"]
