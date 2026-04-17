import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db, async_session
from app.api.deps import get_current_user_id
from app.teacher.session.transcriber import save_transcript
from app.teacher.session.summarizer import process_unsummarized

router = APIRouter()

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()


@router.post("/sessions/{session_id}/end")
async def end_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """End a learning session: save transcript, then summarize in background."""
    # Save transcript synchronously (fast — just DB query + file write)
    path = await save_transcript(db, user_id, session_id)

    # Fire-and-forget: summarize in background
    async def _summarize():
        try:
            async with async_session() as bg_db:
                await process_unsummarized(bg_db, user_id)
        except Exception as e:
            print(f"[sessions] background summarize error: {e}", flush=True)

    task = asyncio.get_event_loop().create_task(_summarize())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "ok", "transcript": str(path)}
