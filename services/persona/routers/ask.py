import asyncio
import json
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from infra.db import get_db, async_session
from persona.teacher.models.interaction import Interaction
from persona.teacher.schemas import AskRequest, AskResponse
from persona.teacher import assemble_context, parse_title
from persona.teacher.silicon_brain_client import SiliconBrainClient
from infra.model.llm import generate_cached, stream_cached
from persona.teacher.brain_builder.background import post_interaction_update
from infra.auth import parse_user_id as get_current_user_id

router = APIRouter()

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()


def _get_client(request: Request) -> SiliconBrainClient:
    client = getattr(request.app.state, "brain_client", None)
    if client is None:
        client = SiliconBrainClient()
        request.app.state.brain_client = client
    return client


@router.post("/ask/stream")
async def ask_stream(
    body: AskRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """SSE endpoint with streaming — detects proxy search events."""
    client = _get_client(request)
    ctx = await assemble_context(body, user_id, db, client)

    status_queue: asyncio.Queue = asyncio.Queue()

    async def run_generation():
        answer = ""
        answer_body = ""
        extracted_title: str | None = None
        usage: dict = {}
        try:
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
                        # Only attempt parse_title once we've seen a newline.
                        # parse_title's regex matches against `$` (end-of-string)
                        # as well as `\n+`, which means partial mid-stream
                        # buffers like "TITLE: Dec" wrongly succeed before
                        # "oder Stacks" arrives. Wait for the newline boundary.
                        if "\n" in head_buffer:
                            title, body_text = parse_title(head_buffer)
                            if title is not None:
                                extracted_title = title
                                await status_queue.put({"type": "title", "title": title})
                                title_resolved = True
                                if body_text:
                                    await status_queue.put({"type": "token", "text": body_text})
                            elif not head_buffer.lstrip().upper().startswith("TITLE:"):
                                # Model didn't honor the format — flush as-is.
                                await status_queue.put({"type": "token", "text": head_buffer})
                                title_resolved = True
                    else:
                        await status_queue.put({"type": "token", "text": chunk})
                elif evt["kind"] == "done":
                    answer = evt["text"]
                    usage = evt["usage"]
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
            await status_queue.put({
                "type": "debug",
                "static_system": ctx.parts.static_system,
                "static_user_passage": ctx.parts.static_user_passage,
                "dynamic_user": ctx.parts.dynamic_user,
                "prior_message_count": len(ctx.prior_messages),
                "usage": usage,
            })
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

                await status_queue.put({
                    "type": "interaction",
                    "interaction_id": str(interaction.id),
                })

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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    client = _get_client(request)
    ctx = await assemble_context(body, user_id, db, client)
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
