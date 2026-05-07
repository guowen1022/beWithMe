"""Reflect-scenario context assembly — minimal pipeline.

The reflect path doesn't need RAG (no question to embed), no doc-chunk
search (no document focus), no session history (reflect is not a chat
turn). Just enough state to reason about what to do given the events
that fired.

Fetches: profile (for self_description), user_profile, concept_nodes,
canvas_state. Assembles the reflect prompt and returns a TeacherContext.
"""
from __future__ import annotations

import uuid
from typing import List

from persona.teacher.contexts.answer import TeacherContext
from persona.teacher.preferences import get_user_profile
from persona.teacher.knowledge import get_concepts
from persona.teacher.prompts.reflect import (
    PerceptionEventSummary,
    build as build_reflect_prompt,
)
from persona.teacher.silicon_brain_client import SiliconBrainClient
from workshop.canvas.tools.read_media import read_media

from infra.db import async_session


async def assemble(
    user_id: uuid.UUID,
    events: List[PerceptionEventSummary],
) -> TeacherContext:
    """Build the reflect-scenario context.

    Self-contained: opens its own DB session and silicon_brain client,
    closes them before returning. The trigger pipeline is fire-and-
    forget so it doesn't pass these in.
    """
    # 1. Canvas state — what's actually on screen right now.
    canvas_state = None
    try:
        canvas_state = await read_media(user_id)
    except Exception as e:
        print(f"[teacher.reflect] read_media error: {e}", flush=True)

    # 2. Teacher's own DB reads.
    user_profile = None
    concept_nodes: list = []
    try:
        async with async_session() as db:
            user_profile = await get_user_profile(db, user_id, session_id=None)
            concept_nodes = await get_concepts(db, user_id, limit=30)
    except Exception as e:
        print(f"[teacher.reflect] db read error: {e}", flush=True)

    # 3. Self-description + per-device talk preference from silicon_brain.
    self_description = ""
    talk_preference: dict | None = None
    client = SiliconBrainClient()
    try:
        try:
            profile = await client.get_profile(user_id)
            self_description = profile.self_description if profile else ""
        except Exception as e:
            print(f"[teacher.reflect] get_profile error: {e}", flush=True)
        try:
            talk_preference = await client.get_talk_preference(user_id)
        except Exception as e:
            print(f"[teacher.reflect] get_talk_preference error: {e}", flush=True)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    # 4. Build the prompt.
    parts = build_reflect_prompt(
        events=events,
        canvas_state=canvas_state,
        user_profile=user_profile,
        concept_nodes=concept_nodes,
        self_description=self_description,
        talk_preference=talk_preference,
    )

    # Reflect turns have no chat history — they are not user-initiated.
    return TeacherContext(parts=parts, prior_messages=[])


__all__ = ["assemble"]
