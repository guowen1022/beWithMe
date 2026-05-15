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
from persona.teacher import notices as teacher_notices
from persona.teacher.prompts.reflect import (
    PerceptionEventSummary,
    build as build_reflect_prompt,
)
from infra.silicon_brain_client import SiliconBrainClient
from workshop.canvas.tools.read_media import read_media

from infra.db import async_session
from infra.perception import read_for_user as read_perception


async def assemble(
    user_id: uuid.UUID,
    events: List[PerceptionEventSummary],
    voice_leads: bool = False,
) -> TeacherContext:
    """Build the reflect-scenario context.

    Self-contained: opens its own DB session and silicon_brain client,
    closes them before returning. The trigger pipeline is fire-and-
    forget so it doesn't pass these in.

    When `voice_leads=True`, the prompt is built with the brief/tools-free
    variant so Lane A's spoken turn can stream prose without waiting on
    tool-call arg generation. The canvas-writer pass runs separately.
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
    related_notes: list = []
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

        # 3b. Semantically related notes from this user's prior teaching.
        # Query is the recent user-speech text (joined, capped). The
        # knowledge sidecar applies nomic's `search_query:` prefix
        # internally; matches use cosine similarity against chunks indexed
        # with the `search_document:` prefix.
        try:
            snapshot = read_perception(user_id)
            log = snapshot.get("user_speech_log") or []
            recent_texts = [u.text for u in list(log)[-3:] if getattr(u, "text", None)]
            query_text = " ".join(reversed(recent_texts))[:300].strip()
            if query_text:
                hits = await client.search_notes(user_id, query_text, top_k=3)
                related_notes = [
                    {"slug": h.note_id, "score": h.score, "text": h.text}
                    for h in hits if h.score >= 0.40
                ]
        except Exception as e:
            print(f"[teacher.reflect] search_notes error: {e}", flush=True)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    # 4. Recent user-speech utterances — in-memory only, this session.
    #    Cap to the last 10 to keep the prompt cheap; talk is cheap and
    #    older context isn't worth the tokens.
    recent_user_speech = []
    try:
        snapshot = read_perception(user_id)
        log = snapshot.get("user_speech_log") or []
        recent_user_speech = list(log)[-10:]
    except Exception as e:
        print(f"[teacher.reflect] read user_speech_log error: {e}", flush=True)

    # 5. Drain pending Lane B notices. Lane B (background pool) appends
    #    one-line summaries of completed work; Lane A surfaces them in the
    #    next reply when relevant. Drain (not peek) so we don't surface
    #    the same notice twice.
    recent_notices: list[str] = []
    try:
        recent_notices = teacher_notices.drain(user_id)
    except Exception as e:
        print(f"[teacher.reflect] drain notices error: {e}", flush=True)

    # 6. Build the prompt.
    parts = build_reflect_prompt(
        events=events,
        canvas_state=canvas_state,
        user_profile=user_profile,
        concept_nodes=concept_nodes,
        self_description=self_description,
        talk_preference=talk_preference,
        recent_user_speech=recent_user_speech,
        recent_notices=recent_notices,
        related_notes=related_notes,
        voice_leads=voice_leads,
    )

    # Reflect turns have no chat history — they are not user-initiated.
    return TeacherContext(parts=parts, prior_messages=[])


__all__ = ["assemble"]
