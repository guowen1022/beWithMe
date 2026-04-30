import asyncio
import re
from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from typing import Optional
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from infra.db import get_db
from infra.auth import parse_user_id as get_current_user_id
from persona.teacher.session.transcriber import save_transcript
from persona.teacher.session.summarizer import process_unsummarized
from persona.teacher.silicon_brain_client import SiliconBrainClient
from silicon_brain.models.session_summary import SessionSummary

router = APIRouter()

# Hold references to background tasks so they don't get garbage collected
_background_tasks: set = set()


def _get_client(request: Request) -> SiliconBrainClient:
    client = getattr(request.app.state, "brain_client", None)
    if client is None:
        client = SiliconBrainClient()
        request.app.state.brain_client = client
    return client


@router.post("/sessions/{session_id}/end")
async def end_session(
    session_id: UUID,
    request: Request,
    user_id: UUID = Depends(get_current_user_id),
):
    """End a learning session: save transcript, then summarize in background."""
    client = _get_client(request)
    path = await save_transcript(user_id, session_id, client)

    async def _summarize():
        try:
            await process_unsummarized(user_id, client)
        except Exception as e:
            print(f"[sessions] background summarize error: {e}", flush=True)

    task = asyncio.get_event_loop().create_task(_summarize())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "ok", "transcript": str(path)}


# --- Meta parsing helpers ---

_TITLE_RE = re.compile(r"\*\*Title\*\*:\s*(.+)", re.IGNORECASE)
_LABELS_RE = re.compile(r"\*\*Labels\*\*:\s*(.+)", re.IGNORECASE)
_TOPICS_RE = re.compile(r"\*\*Topics\*\*:\s*(.+)", re.IGNORECASE)


def _parse_meta(summary_text: str) -> dict:
    """Extract Title, Labels, and Topics from the ## Meta section."""
    title = ""
    labels: list[str] = []
    topics = ""
    head = summary_text[:600]
    m = _TITLE_RE.search(head)
    if m:
        title = m.group(1).strip()
    m = _LABELS_RE.search(head)
    if m:
        labels = [l.strip() for l in m.group(1).split(",") if l.strip()]
    m = _TOPICS_RE.search(head)
    if m:
        topics = m.group(1).strip()
    return {"title": title, "labels": labels, "topics": topics}


@router.get("/sessions/summaries/graph")
async def session_graph(
    label: Optional[str] = Query(None, description="Filter by label"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Return session summary nodes grouped by labels for canvas visualization.

    Optional `?label=NLP` filters to sessions that have that label.
    """
    stmt = (
        select(SessionSummary)
        .where(SessionSummary.user_id == user_id)
        .order_by(SessionSummary.created_at.asc())
    )
    if label:
        stmt = stmt.where(SessionSummary.labels.any(label))
    result = await db.execute(stmt)
    summaries = list(result.scalars().all())

    if not summaries:
        return {"nodes": []}

    session_ids = [str(s.session_id) for s in summaries]
    duration_map: dict[str, int] = {}
    if session_ids:
        dur_result = await db.execute(text("""
            SELECT session_id,
                   EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at))) / 60 AS minutes
            FROM interactions
            WHERE user_id = :user_id
            GROUP BY session_id
        """), {"user_id": str(user_id)})
        for row in dur_result.fetchall():
            sid, mins = row
            duration_map[str(sid)] = max(1, round(float(mins)))

    nodes = []
    for s in summaries:
        path = Path(s.file_path)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        meta = _parse_meta(content)
        labels = s.labels if s.labels else (meta.get("labels") or ["General"])
        nodes.append({
            "session_id": str(s.session_id),
            "title": meta["title"] or f"Session {str(s.session_id)[:8]}",
            "labels": labels,
            "created_at": s.created_at.isoformat() if s.created_at else "",
            "duration_min": duration_map.get(str(s.session_id), 0),
            "summary": content,
        })

    return {"nodes": nodes}
