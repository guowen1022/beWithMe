"""Per-phase helpers for the answer-scenario context pipeline.

`contexts/answer.py:assemble()` is the orchestrator; this module holds each
numbered phase as a named, independently-testable helper. Every helper times
itself into `phases[...]` (no-op when `phases` is None) so the latency
attribution the benchmark relies on is unchanged. Behavior is identical to the
original inline pipeline — this is a pure readability extraction (F6).
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.rag.embedding import embed_text
from infra.silicon_brain_client import SiliconBrainClient
from persona.teacher.knowledge import get_concepts, get_graph_context
from persona.teacher.models.interaction import Interaction
from persona.teacher.preferences import boost_query_embedding, get_user_profile
from persona.teacher.prompts.answer import build as build_answer_prompt
from persona.teacher.prompts.parts import PromptParts, build_history_messages
from persona.teacher.prompts.voice_answer import build as build_voice_answer_prompt
from persona.teacher.prompts.voice_brief import build as build_voice_brief_prompt
from infra.contracts.ask import AskRequest
from workshop.canvas.tools.read_media import read_media


@contextmanager
def _phase(phases: Optional[dict], key: str):
    """Time a block and write the elapsed ms into `phases[key]`. No-op when
    `phases` is None — instrumentation cost = one perf_counter pair only when
    a caller wants the numbers (i.e. the benchmark)."""
    if phases is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        phases[key] = round((time.perf_counter() - t0) * 1000, 2)


async def load_self_description(
    client: SiliconBrainClient, user_id: uuid.UUID, phases: Optional[dict] = None,
) -> str:
    """1a. Self-description — silicon_brain user data."""
    with _phase(phases, "ctx_profile_ms"):
        try:
            profile = await client.get_profile(user_id)
            return profile.self_description if profile else ""
        except Exception as e:
            print(f"[teacher] get_profile error: {e}", flush=True)
            return ""


async def load_talk_preference(
    client: SiliconBrainClient, user_id: uuid.UUID, phases: Optional[dict] = None,
) -> Optional[dict]:
    """1a'. Per-device talk-channel preference (desktop/tablet/phone → voice|text|both)."""
    with _phase(phases, "ctx_talk_pref_ms"):
        try:
            return await client.get_talk_preference(user_id)
        except Exception as e:
            print(f"[teacher] get_talk_preference error: {e}", flush=True)
            return None


async def load_user_profile(
    db: AsyncSession, user_id: uuid.UUID, session_id, phases: Optional[dict] = None,
):
    """1b. Teacher's preference state (categorical + embedding)."""
    with _phase(phases, "ctx_user_profile_ms"):
        return await get_user_profile(db, user_id, session_id=session_id)


async def load_concepts_and_graph(
    db: AsyncSession, user_id: uuid.UUID, phases: Optional[dict] = None,
):
    """1c + 1d. Concept mastery snapshot + the relational graph context."""
    with _phase(phases, "ctx_concepts_ms"):
        concept_nodes = await get_concepts(db, user_id, limit=30)
    with _phase(phases, "ctx_graph_ms"):
        graph_ctx = ""
        if concept_nodes:
            try:
                concept_names = [c.name for c in concept_nodes[:10]]
                graph_ctx = await get_graph_context(db, user_id, concept_names)
            except Exception as e:
                print(f"[teacher] graph walk error: {e}", flush=True)
    return concept_nodes, graph_ctx


async def embed_query(
    db: AsyncSession, user_id: uuid.UUID, body: AskRequest, phases: Optional[dict] = None,
) -> Optional[list]:
    """2 + 3. Embed the query, then boost it by the teacher's preference embedding."""
    embed_context = body.selected_text or body.passage_text or ""
    query_text = (embed_context + " " + body.question) if embed_context else body.question
    with _phase(phases, "ctx_embed_ms"):
        try:
            query_embedding = await embed_text(query_text)
        except Exception:
            query_embedding = None
    with _phase(phases, "ctx_boost_embed_ms"):
        if query_embedding:
            try:
                query_embedding = await boost_query_embedding(db, user_id, query_embedding)
            except Exception as e:
                print(f"[teacher] boost embedding error: {e}", flush=True)
    return query_embedding


async def retrieve_doc_chunks(
    client: SiliconBrainClient, user_id: uuid.UUID, body: AskRequest,
    query_embedding: Optional[list], phases: Optional[dict] = None,
) -> list:
    """4. Retrieve relevant document chunks for the active document."""
    with _phase(phases, "ctx_doc_search_ms"):
        doc_chunks: list = []
        if body.document_id and query_embedding:
            try:
                doc_chunks = await client.search_document_chunks(
                    user_id, body.document_id, query_embedding, top_k=5
                )
            except Exception as e:
                print(f"[teacher] doc-chunk search error: {e}", flush=True)
        return doc_chunks


async def merge_past_summaries(
    db: AsyncSession, user_id: uuid.UUID, query_embedding: Optional[list],
    graph_ctx: str, phases: Optional[dict] = None,
) -> str:
    """5. Vector-search past learning sessions; append them into `graph_ctx`."""
    with _phase(phases, "ctx_past_summaries_ms"):
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
    return graph_ctx


async def load_session_history(
    db: AsyncSession, user_id: uuid.UUID, session_id, phases: Optional[dict] = None,
) -> List[dict]:
    """6. Session history → prompt-ready prior messages."""
    with _phase(phases, "ctx_history_ms"):
        prior_interactions = await _fetch_session_history(db, user_id, session_id)
        return build_history_messages(prior_interactions)


async def read_canvas_state(
    user_id: uuid.UUID, body: AskRequest, phases: Optional[dict] = None,
):
    """7. Live canvas perception state (+ the debug trace written to disk)."""
    canvas_state = None
    with _phase(phases, "ctx_canvas_ms"):
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
    return canvas_state


def build_prompt(
    body: AskRequest, voice_leads: bool, voice_mode: bool, *,
    self_description: str, doc_chunks: list, user_profile, concept_nodes,
    graph_context: str, canvas_state, talk_preference, phases: Optional[dict] = None,
) -> PromptParts:
    """8. Dispatch to the right prompt builder and build the PromptParts.

      voice_leads=True  → voice_brief (brevity-first spoken answer; canvas painted by a writer pass)
      voice_mode=True   → voice_answer (single-turn voice with full tool palette — legacy)
      otherwise         → answer (text mode)
    """
    if voice_leads:
        builder = build_voice_brief_prompt
    elif voice_mode:
        builder = build_voice_answer_prompt
    else:
        builder = build_answer_prompt
    with _phase(phases, "ctx_build_prompt_ms"):
        return builder(
            passage=body.passage_text,
            selected_text=body.selected_text,
            question=body.question,
            self_description=self_description,
            doc_chunks=doc_chunks,
            user_profile=user_profile,
            concept_nodes=concept_nodes,
            graph_context=graph_context,
            canvas_state=canvas_state,
            talk_preference=talk_preference,
        )


async def apply_maestro_frame(user_id: uuid.UUID, parts: PromptParts) -> PromptParts:
    """9. PR-5 Maestro cache overlay. When an inbox tap has seeded the cache,
    the active candidate's opening + posture rides into the top of the system
    prompt. Absent cache → unchanged. Cache failures never block the turn."""
    try:
        from persona.teacher.engagement import read_active_cache
        from persona.teacher.prompts import maestro_frame
        entry = await read_active_cache(user_id, "teacher:long-horizon-propose")
        frame = maestro_frame.render(entry)
        if frame:
            # PromptParts is a NamedTuple — immutable. Replace via _replace.
            parts = parts._replace(static_system=frame + "\n\n" + parts.static_system)
    except Exception as e:
        print(f"[teacher.context] maestro cache read failed: {e}", flush=True)
    return parts


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
