"""Session summarizer — turns transcripts into summaries; persists to LearningSession.

Three clean layers:
  1. summarize_transcript(text)              — pure LLM call (text → text)
  2. summarize_file(t_path, s_path)          — file op (read transcript, write summary)
  3. index_summary(db, user_id, session_id, s_path)
                                              — embed + upsert LearningSession (own DB)

`process_unsummarized()` scans the user's session directory for transcripts
missing a summary and runs the three layers.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config import settings
from infra.db import async_session
from infra.model.llm import generate

from persona.teacher.models.learning_session import LearningSession
from persona.teacher.session.transcriber import DATA_DIR
from workshop import load_skill


# Local Ollama embedding client.
_embed_client: Optional[httpx.AsyncClient] = None


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None:
        _embed_client = httpx.AsyncClient(base_url=settings.ollama_url, timeout=30.0)
    return _embed_client


async def _embed_text(text_input: str) -> List[float]:
    client = _get_embed_client()
    resp = await client.post(
        "/api/embed", json={"model": settings.embedding_model, "input": text_input}
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


# -------- Layer 1: pure LLM call --------

async def summarize_transcript(
    transcript_text: str,
    user_id: Optional[uuid.UUID] = None,
) -> str:
    skill_prompt = load_skill("teacher/summarize_session")
    return await generate(
        transcript_text, system=skill_prompt, max_tokens=4096,
        purpose="session-summarizer", user_id=user_id,
    )


# -------- Layer 2: file operation --------

async def summarize_file(
    transcript_path: Path,
    summary_path: Path,
    user_id: Optional[uuid.UUID] = None,
) -> str:
    transcript_text = transcript_path.read_text(encoding="utf-8")
    summary_text = await summarize_transcript(transcript_text, user_id=user_id)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary_text, encoding="utf-8")
    return summary_text


# -------- Layer 3: index (write to LearningSession, own DB) --------

async def index_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    summary_path: Path,
) -> LearningSession:
    """Embed the summary file, extract labels, upsert LearningSession (own DB)."""
    summary_text = summary_path.read_text(encoding="utf-8")
    embedding = await _embed_text(summary_text)

    labels: list[str] = []
    labels_match = re.search(r"\*\*Labels\*\*:\s*(.+)", summary_text[:600], re.IGNORECASE)
    if labels_match:
        labels = [l.strip() for l in labels_match.group(1).split(",") if l.strip()]

    # Upsert by (user_id, session_id) UNIQUE.
    result = await db.execute(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.session_id == session_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = LearningSession(
            user_id=user_id,
            session_id=session_id,
            file_path=str(summary_path),
            labels=labels,
            embedding=embedding,
        )
        db.add(row)
    else:
        row.file_path = str(summary_path)
        row.labels = labels
        row.embedding = embedding

    await db.commit()
    await db.refresh(row)
    return row


# -------- Orchestrator --------

async def process_unsummarized(user_id: uuid.UUID) -> list[str]:
    """Find transcripts without a summary and process each. Idempotent.

    Opens its own DB session — typically called from a background task after
    the originating request has completed.
    """
    user_dir = DATA_DIR / str(user_id)
    if not user_dir.exists():
        return []

    summarized: list[str] = []

    for session_dir in user_dir.iterdir():
        if not session_dir.is_dir():
            continue
        t_path = session_dir / "transcript.md"
        s_path = session_dir / "summary.md"

        if not t_path.exists() or s_path.exists():
            continue

        try:
            session_id = uuid.UUID(session_dir.name)
        except ValueError:
            continue

        try:
            await summarize_file(t_path, s_path, user_id=user_id)
            async with async_session() as db:
                await index_summary(db, user_id, session_id, s_path)
            summarized.append(str(session_id))
            print(f"[summarizer] Summarized session {session_id} → {s_path}", flush=True)
        except Exception as e:
            print(f"[summarizer] Failed to summarize session {session_id}: {e}", flush=True)

    return summarized
