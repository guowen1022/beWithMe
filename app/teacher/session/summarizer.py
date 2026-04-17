"""Session summarizer — async process that turns transcripts into summaries.

Three clean layers:
  1. summarize_transcript(text)        → pure LLM call (text → text)
  2. summarize_file(t_path, s_path)    → file op (read transcript, write summary)
  3. index_summary(db, ..., s_path)    → DB op (embed summary, upsert index row)

An orchestrator `process_unsummarized()` scans the user's session directory
for transcripts missing a summary and delegates to the layers above.

The summarizer's input is strictly the transcript file — nothing else.
All instructions for the LLM live in `app/teacher/skills/summarize_session.md`.
"""

import uuid
from pathlib import Path
from typing import List, Optional
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.llm import generate
from app.teacher.session.models import SessionSummary
from app.teacher.session.transcriber import DATA_DIR

# Path to the skill prompt — the sole source of instructions for the LLM
_SKILL_PROMPT_PATH = Path(__file__).resolve().parent.parent / "skills" / "summarize_session.md"

# Own embedding client (copied pattern from silicon_brain/services/embedding.py)
_embed_client: Optional[httpx.AsyncClient] = None


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None:
        _embed_client = httpx.AsyncClient(base_url=settings.ollama_url, timeout=30.0)
    return _embed_client


async def _embed_text(text_input: str) -> List[float]:
    client = _get_embed_client()
    resp = await client.post("/api/embed", json={"model": settings.embedding_model, "input": text_input})
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def load_skill_prompt() -> str:
    """Read the summarization skill prompt from disk."""
    return _SKILL_PROMPT_PATH.read_text(encoding="utf-8")


# -------- Layer 1: pure LLM call --------

async def summarize_transcript(transcript_text: str) -> str:
    """Pure transformation: transcript text → summary markdown.

    The LLM sees exactly two things:
      - system: the skill prompt (all instructions)
      - user:   the transcript text (all content)

    No extra instructions, no pre/post processing.
    """
    skill_prompt = load_skill_prompt()
    return await generate(transcript_text, system=skill_prompt, max_tokens=4096)


# -------- Layer 2: file operation --------

async def summarize_file(transcript_path: Path, summary_path: Path) -> str:
    """Read transcript.md, summarize it, write summary.md. Return summary text."""
    transcript_text = transcript_path.read_text(encoding="utf-8")
    summary_text = await summarize_transcript(transcript_text)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary_text, encoding="utf-8")
    return summary_text


# -------- Layer 3: index operation --------

async def index_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    summary_path: Path,
) -> None:
    """Embed the summary file and upsert its SessionSummary index row."""
    summary_text = summary_path.read_text(encoding="utf-8")
    embedding = await _embed_text(summary_text)

    row = SessionSummary(
        user_id=user_id,
        session_id=session_id,
        file_path=str(summary_path),
        embedding=embedding,
    )
    db.add(row)
    await db.commit()


# -------- Orchestrator --------

async def process_unsummarized(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Find transcripts without a summary and process each one.

    The existence of `summary.md` on disk is the single source of truth for
    "already summarized" — so re-running this is idempotent.

    Returns list of session_id strings that were summarized this run.
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
            await summarize_file(t_path, s_path)
            await index_summary(db, user_id, session_id, s_path)
            summarized.append(str(session_id))
            print(f"[summarizer] Summarized session {session_id} → {s_path}", flush=True)
        except Exception as e:
            print(f"[summarizer] Failed to summarize session {session_id}: {e}", flush=True)
            await db.rollback()

    return summarized


# -------- Retrieval (unchanged) --------

async def search_past_summaries(
    db: AsyncSession,
    user_id: uuid.UUID,
    query_embedding: List[float],
    top_k: int = 3,
) -> list[dict]:
    """Search past session summaries by embedding similarity.

    Returns list of dicts with session_id, file_path, similarity, and content.
    """
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

    summaries = []
    for row in rows:
        session_id, file_path, distance = row
        path = Path(file_path)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        summaries.append({
            "session_id": str(session_id),
            "file_path": file_path,
            "similarity": 1.0 - distance,
            "content": content,
        })

    return summaries
