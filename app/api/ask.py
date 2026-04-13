import asyncio
import json
import sys
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db, async_session
from app.models.profile import Profile
from app.models.interaction import Interaction
from app.schemas.query import AskRequest, AskResponse
from app.services.embedding import embed_text
from app.services.retrieval import search_document_chunks
from app.services.prompt_builder import (
    build_answer_prompt,
    build_history_messages,
    parse_title,
)
from app.services.llm import generate_cached, stream_cached
from app.user_profile import get_user_profile, boost_query_embedding
from app.knowledge import get_graph_context, get_concepts
from app.background.post_interaction import post_interaction_update
from app.api.deps import get_current_user_id

router = APIRouter()

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()


async def _fetch_session_history(
    db: AsyncSession, user_id: UUID, session_id: UUID
) -> list[Interaction]:
    """All prior interactions in this session, in chronological order.

    The recursive-question feature treats every Q&A in a session as one
    flat conversation from the model's perspective, regardless of how the
    user navigated the tree in the UI. This is the source for the messages
    array sent to the LLM.
    """
    stmt = (
        select(Interaction)
        .where(Interaction.user_id == user_id, Interaction.session_id == session_id)
        .order_by(Interaction.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _build_context(body: AskRequest, db: AsyncSession, user_id: UUID):
    result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one_or_none()
    self_description = profile.self_description if profile else ""

    embed_context = body.selected_text or body.passage_text or ""
    query_text = (embed_context + " " + body.question) if embed_context else body.question
    try:
        query_embedding = await embed_text(query_text)
    except Exception:
        query_embedding = None

    # Boost query with user's preference embedding for personalized retrieval
    if query_embedding:
        query_embedding = await boost_query_embedding(db, user_id, query_embedding)

    doc_chunks = []
    if body.document_id and query_embedding:
        doc_chunks = await search_document_chunks(db, body.document_id, query_embedding, top_k=5)

    # Fetch user profile (static preferences + embedding + session signals)
    user_profile = await get_user_profile(db, user_id, session_id=body.session_id)

    # Fetch concepts from knowledge module (dynamic learning state)
    concept_nodes = await get_concepts(db, user_id, limit=30)

    # Walk the concept graph for related territory
    graph_ctx = ""
    if concept_nodes:
        try:
            concept_names = [c.name for c in concept_nodes[:10]]
            graph_ctx = await get_graph_context(db, user_id, concept_names)
        except Exception as e:
            print(f"[ask] graph walk error: {e}", flush=True)

    # Flat session history → multi-turn messages. The tree the user sees in
    # the UI is collapsed into one chronological dialogue here.
    prior_interactions = await _fetch_session_history(db, user_id, body.session_id)
    prior_messages = build_history_messages(prior_interactions)

    parts = build_answer_prompt(
        passage=body.passage_text,
        selected_text=body.selected_text,
        question=body.question,
        self_description=self_description,
        doc_chunks=doc_chunks,
        user_profile=user_profile,
        concept_nodes=concept_nodes,
        graph_context=graph_ctx,
    )
    return parts, prior_messages


@router.post("/ask/stream")
async def ask_stream(
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """SSE endpoint with streaming — detects proxy search events."""
    parts, prior_messages = await _build_context(body, db, user_id)

    status_queue: asyncio.Queue = asyncio.Queue()

    async def run_generation():
        answer = ""
        answer_body = ""
        extracted_title: str | None = None
        usage: dict = {}
        try:
            # The model's first line is `TITLE: …\n`. Buffer leading deltas
            # until we've isolated the title, then emit it as a `title` SSE
            # event and stream the remainder as normal `token` events.
            title_resolved = False
            head_buffer = ""

            async for evt in stream_cached(
                parts.static_system,
                parts.static_user_passage,
                parts.dynamic_user,
                prior_messages=prior_messages,
            ):
                if evt["kind"] == "delta":
                    chunk = evt["text"]
                    if not title_resolved:
                        head_buffer += chunk
                        title, body_text = parse_title(head_buffer)
                        if title is not None:
                            extracted_title = title
                            await status_queue.put({"type": "title", "title": title})
                            title_resolved = True
                            if body_text:
                                await status_queue.put({"type": "token", "text": body_text})
                        elif "\n" in head_buffer and not head_buffer.lstrip().upper().startswith("TITLE:"):
                            # Model didn't honor the format — flush as-is.
                            await status_queue.put({"type": "token", "text": head_buffer})
                            title_resolved = True
                    else:
                        await status_queue.put({"type": "token", "text": chunk})
                elif evt["kind"] == "done":
                    answer = evt["text"]
                    usage = evt["usage"]
                    # If the model never produced a newline mid-stream (very
                    # short answer), flush whatever's still in the head buffer.
                    if not title_resolved and head_buffer:
                        title, body_text = parse_title(head_buffer)
                        if title is not None:
                            extracted_title = title
                            await status_queue.put({"type": "title", "title": title})
                            if body_text:
                                await status_queue.put({"type": "token", "text": body_text})
                        else:
                            await status_queue.put({"type": "token", "text": head_buffer})
                        title_resolved = True

            # Authoritative title from the full answer (handles edge cases
            # where the streamed parse missed it). `answer_body` is what we
            # persist and what the frontend treats as the canonical text:
            # the TITLE line is metadata, not part of the answer prose.
            final_title, answer_body = parse_title(answer)
            if extracted_title is None:
                extracted_title = final_title
            if not answer_body:
                answer_body = answer

            print(
                f"[ask/stream] answer length={len(answer_body)}, "
                f"title={extracted_title!r}, usage={usage}, first 100={answer_body[:100]!r}",
                flush=True,
            )
            # Debug event for the LLM tab (full prompt parts + token usage).
            await status_queue.put({
                "type": "debug",
                "static_system": parts.static_system,
                "static_user_passage": parts.static_user_passage,
                "dynamic_user": parts.dynamic_user,
                "prior_message_count": len(prior_messages),
                "usage": usage,
            })
            # Final answer — reconciles the accumulated stream. With flat
            # session history we no longer surface separate similar matches.
            await status_queue.put({
                "type": "answer",
                "answer": answer_body,
                "title": extracted_title,
                "related_interaction_ids": [],
            })
        except Exception as e:
            print(f"[ask/stream] error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            await status_queue.put({"type": "error", "message": str(e)})

        # Store interaction and schedule background processing
        try:
            async with async_session() as bg_db:
                interaction = Interaction(
                    user_id=user_id,
                    session_id=body.session_id,
                    parent_interaction_id=body.parent_interaction_id,
                    title=extracted_title,
                    passage_text=body.passage_text,
                    question=body.question,
                    answer=answer_body or answer,
                    source_document=str(body.document_id) if body.document_id else None,
                )
                bg_db.add(interaction)
                await bg_db.commit()
                await bg_db.refresh(interaction)

                # Echo the persisted id so the frontend can use it as the
                # parent_interaction_id for any drill-down it spawns next.
                await status_queue.put({
                    "type": "interaction",
                    "interaction_id": str(interaction.id),
                })

                # Fire-and-forget: schedule background task on the event loop
                print(f"[ask/stream] scheduling background task for {interaction.id}", flush=True)
                task = asyncio.get_event_loop().create_task(
                    post_interaction_update(interaction.id, user_id)
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
                print(f"[ask/stream] background task scheduled", flush=True)
        except Exception as e:
            print(f"[ask/stream] store error: {e}", flush=True)
            import traceback
            traceback.print_exc()

        await status_queue.put(None)

    async def event_stream():
        task = asyncio.create_task(run_generation())
        while True:
            msg = await status_queue.get()
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    parts, prior_messages = await _build_context(body, db, user_id)
    answer, _ = await generate_cached(
        parts.static_system,
        parts.static_user_passage,
        parts.dynamic_user,
        prior_messages=prior_messages,
    )
    title, _ = parse_title(answer)

    interaction = Interaction(
        user_id=user_id,
        session_id=body.session_id,
        parent_interaction_id=body.parent_interaction_id,
        title=title,
        passage_text=body.passage_text,
        question=body.question,
        answer=answer,
        source_document=str(body.document_id) if body.document_id else None,
    )
    db.add(interaction)
    await db.commit()
    await db.refresh(interaction)

    background_tasks.add_task(post_interaction_update, interaction.id, user_id)

    return AskResponse(
        interaction_id=interaction.id,
        answer=answer,
        session_id=body.session_id,
        title=title,
        related_interaction_ids=[],
    )
