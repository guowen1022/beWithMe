"""Answer-scenario context assembly — the heavy pipeline.

Reads from silicon_brain (HTTP) + the teacher's own DB and assembles
everything `prompts.answer.build` needs: profile, preferences, concepts,
graph context, embedded query, doc-chunk RAG, past summaries, session
history, and the live canvas state.

Body is the original `assemble_context` body from `agent.py:47-175`,
moved here verbatim and pointed at `prompts.answer.build`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.rag.embedding import embed_text
from persona.teacher.knowledge import get_concepts, get_graph_context
from persona.teacher.models.interaction import Interaction
from persona.teacher.preferences import boost_query_embedding, get_user_profile
from persona.teacher.prompts.answer import build as build_answer_prompt
from persona.teacher.prompts.parts import PromptParts, build_history_messages
from persona.teacher.schemas import AskRequest
from persona.teacher.silicon_brain_client import SiliconBrainClient
from workshop.canvas.tools.read_media import read_media


@dataclass
class TeacherContext:
    """Everything the teacher needs to generate an answer."""
    parts: PromptParts
    prior_messages: List[dict]


async def assemble(
    body: AskRequest,
    user_id: uuid.UUID,
    db: AsyncSession,
    client: SiliconBrainClient,
) -> TeacherContext:
    """Read silicon_brain (HTTP) + teacher's own DB and build the
    answer-scenario prompt. The full RAG + history pipeline."""
    # 1a. Self-description — silicon_brain user data.
    try:
        profile = await client.get_profile(user_id)
        self_description = profile.self_description if profile else ""
    except Exception as e:
        print(f"[teacher] get_profile error: {e}", flush=True)
        self_description = ""

    # 1a'. Per-device talk-channel preference (desktop/tablet/phone → voice|text|both).
    try:
        talk_preference = await client.get_talk_preference(user_id)
    except Exception as e:
        print(f"[teacher] get_talk_preference error: {e}", flush=True)
        talk_preference = None

    # 1b. Teacher's preference state (categorical + embedding).
    user_profile = await get_user_profile(db, user_id, session_id=body.session_id)

    # 1c. Concept mastery snapshot.
    concept_nodes = await get_concepts(db, user_id, limit=30)

    # 1d. Graph context.
    graph_ctx = ""
    if concept_nodes:
        try:
            concept_names = [c.name for c in concept_nodes[:10]]
            graph_ctx = await get_graph_context(db, user_id, concept_names)
        except Exception as e:
            print(f"[teacher] graph walk error: {e}", flush=True)

    # 2. Embed the query.
    embed_context = body.selected_text or body.passage_text or ""
    query_text = (embed_context + " " + body.question) if embed_context else body.question
    try:
        query_embedding = await embed_text(query_text)
    except Exception:
        query_embedding = None

    # 3. Boost the query with the teacher's preference embedding.
    if query_embedding:
        try:
            query_embedding = await boost_query_embedding(db, user_id, query_embedding)
        except Exception as e:
            print(f"[teacher] boost embedding error: {e}", flush=True)

    # 4. Retrieve relevant document chunks.
    doc_chunks: list = []
    if body.document_id and query_embedding:
        try:
            doc_chunks = await client.search_document_chunks(
                user_id, body.document_id, query_embedding, top_k=5
            )
        except Exception as e:
            print(f"[teacher] doc-chunk search error: {e}", flush=True)

    # 5. Past learning sessions.
    if query_embedding:
        try:
            past_summaries = await _search_past_summaries(db, user_id, query_embedding, top_k=2)
            if past_summaries:
                summary_lines = ["RELEVANT PAST LEARNING SESSIONS:"]
                for s in past_summaries:
                    if s.get("content"):
                        summary_lines.append(f"---\n{s['content']}\n---")
                past_ctx = "\n".join(summary_lines)
                graph_ctx = f"{graph_ctx}\n\n{past_ctx}" if graph_ctx else past_ctx
        except Exception as e:
            print(f"[teacher] past summary search error: {e}", flush=True)

    # 6. Session history.
    prior_interactions = await _fetch_session_history(db, user_id, body.session_id)
    prior_messages = build_history_messages(prior_interactions)

    # 7. Current canvas state.
    canvas_state = None
    try:
        canvas_state = await read_media(user_id)
        try:
            import time as _t
            _path = "/tmp/bewithme-perception-trace.log"
            online = [c for c in canvas_state.canvases if c.online]
            lines = []
            ts = _t.strftime("%H:%M:%S")
            lines.append(f"{ts} [ask-turn-start] uid={str(user_id)[:8]} q={(body.question or '')[:80]!r}")
            for c in online:
                for b in c.blocks:
                    s = b.state
                    extra_doc = (s.extra or {}).get("document_id") if s else None
                    lines.append(
                        f"{ts} [canvas-snapshot] did={str(c.device_id)[:8]} "
                        f"bid={b.id} kind={s.kind if s else None} "
                        f"completed={s.completed if s else None} doc_id={extra_doc} "
                        f"age={b.last_updated_s_ago}"
                    )
            if not online:
                lines.append(f"{ts} [canvas-snapshot] uid={str(user_id)[:8]} NO ONLINE CANVASES")
            for ln in lines:
                print(ln, flush=True)
            try:
                with open(_path, "a") as f:
                    f.write("\n".join(lines) + "\n")
            except Exception:
                pass
        except Exception:
            pass
    except Exception as e:
        print(f"[teacher] read_media error (canvas state): {e}", flush=True)

    # 8. Build the prompt.
    parts = build_answer_prompt(
        passage=body.passage_text,
        selected_text=body.selected_text,
        question=body.question,
        self_description=self_description,
        doc_chunks=doc_chunks,
        user_profile=user_profile,
        concept_nodes=concept_nodes,
        graph_context=graph_ctx,
        canvas_state=canvas_state,
        talk_preference=talk_preference,
    )

    return TeacherContext(parts=parts, prior_messages=prior_messages)


async def _fetch_session_history(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
) -> list[Interaction]:
    """Chronological session history."""
    stmt = (
        select(Interaction)
        .where(Interaction.user_id == user_id, Interaction.session_id == session_id)
        .order_by(Interaction.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _search_past_summaries(
    db: AsyncSession,
    user_id: uuid.UUID,
    query_embedding: list[float],
    top_k: int = 3,
) -> list[dict]:
    """Vector search over LearningSession (was SessionSummary)."""
    from pathlib import Path
    from sqlalchemy import text

    stmt = text("""
        SELECT session_id, file_path, embedding <=> :embedding AS distance
        FROM session_summaries
        WHERE user_id = :user_id AND embedding IS NOT NULL
        ORDER BY embedding <=> :embedding
        LIMIT :limit
    """)
    result = await db.execute(stmt, {
        "user_id": str(user_id),
        "embedding": str(query_embedding),
        "limit": top_k,
    })
    rows = result.fetchall()

    out = []
    for session_id, file_path, distance in rows:
        path = Path(file_path)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        out.append({
            "session_id": str(session_id),
            "file_path": file_path,
            "similarity": 1.0 - float(distance),
            "content": content,
        })
    return out


__all__ = ["TeacherContext", "assemble"]
