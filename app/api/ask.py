import asyncio
import json
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db, async_session
from app.models.profile import Profile
from app.models.interaction import Interaction
from app.schemas.query import AskRequest, AskResponse
from app.services.embedding import embed_text
from app.services.retrieval import search_similar_interactions, search_document_chunks
from app.services.prompt_builder import build_answer_prompt
from app.services.llm import generate
from app.background.post_interaction import post_interaction_update

router = APIRouter()


async def _build_context(body: AskRequest, db: AsyncSession):
    result = await db.execute(select(Profile).limit(1))
    profile = result.scalar_one_or_none()
    self_description = profile.self_description if profile else ""

    embed_context = body.selected_text or body.passage_text or ""
    query_text = (embed_context + " " + body.question) if embed_context else body.question
    try:
        query_embedding = await embed_text(query_text)
    except Exception:
        query_embedding = None

    similar = []
    if query_embedding:
        similar = await search_similar_interactions(db, query_embedding, top_k=5)

    doc_chunks = []
    if body.document_id and query_embedding:
        doc_chunks = await search_document_chunks(db, body.document_id, query_embedding, top_k=5)

    system_prompt, user_prompt = build_answer_prompt(
        passage=body.passage_text,
        selected_text=body.selected_text,
        question=body.question,
        self_description=self_description,
        similar_interactions=similar,
        doc_chunks=doc_chunks,
    )
    return system_prompt, user_prompt, similar


@router.post("/ask/stream")
async def ask_stream(body: AskRequest, db: AsyncSession = Depends(get_db)):
    """SSE endpoint with streaming — detects proxy search events."""
    system_prompt, user_prompt, similar = await _build_context(body, db)

    status_queue: asyncio.Queue = asyncio.Queue()

    async def run_generation():
        answer = ""
        try:
            answer = await generate(user_prompt, system=system_prompt)
            print(f"[ask/stream] answer length={len(answer)}, first 100={answer[:100]!r}")
            await status_queue.put({
                "type": "answer",
                "answer": answer,
                "related_interaction_ids": [str(i.id) for i in similar],
            })
        except Exception as e:
            print(f"[ask/stream] error: {e}")
            import traceback
            traceback.print_exc()
            await status_queue.put({"type": "error", "message": str(e)})

        # Store interaction
        try:
            async with async_session() as bg_db:
                interaction = Interaction(
                    session_id=body.session_id,
                    passage_text=body.passage_text,
                    question=body.question,
                    answer=answer,
                    source_document=str(body.document_id) if body.document_id else None,
                )
                bg_db.add(interaction)
                await bg_db.commit()
        except Exception:
            pass

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
):
    system_prompt, user_prompt, similar = await _build_context(body, db)
    answer = await generate(user_prompt, system=system_prompt)

    interaction = Interaction(
        session_id=body.session_id,
        passage_text=body.passage_text,
        question=body.question,
        answer=answer,
        source_document=str(body.document_id) if body.document_id else None,
    )
    db.add(interaction)
    await db.commit()
    await db.refresh(interaction)

    background_tasks.add_task(post_interaction_update, interaction.id)

    return AskResponse(
        interaction_id=interaction.id,
        answer=answer,
        session_id=body.session_id,
        related_interaction_ids=[i.id for i in similar],
    )
