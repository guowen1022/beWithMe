import asyncio
import json
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db, async_session
from app.silicon_brain.models.interaction import Interaction
from app.teacher.schemas import AskRequest, AskResponse
from app.teacher import assemble_context, parse_title
from app.services.llm import generate_cached, stream_cached
from app.brain_builder.background import post_interaction_update
from app.api.deps import get_current_user_id

router = APIRouter()

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()


@router.post("/ask/stream")
async def ask_stream(
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """SSE endpoint with streaming — detects proxy search events."""
    ctx = await assemble_context(body, db, user_id)

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
                ctx.parts.static_system,
                ctx.parts.static_user_passage,
                ctx.parts.dynamic_user,
                prior_messages=ctx.prior_messages,
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
                "static_system": ctx.parts.static_system,
                "static_user_passage": ctx.parts.static_user_passage,
                "dynamic_user": ctx.parts.dynamic_user,
                "prior_message_count": len(ctx.prior_messages),
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
    ctx = await assemble_context(body, db, user_id)
    answer, _ = await generate_cached(
        ctx.parts.static_system,
        ctx.parts.static_user_passage,
        ctx.parts.dynamic_user,
        prior_messages=ctx.prior_messages,
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
