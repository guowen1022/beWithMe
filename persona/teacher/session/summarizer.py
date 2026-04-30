"""Session summarizer — async process that turns transcripts into summaries.

Three clean layers:
  1. summarize_transcript(text)        → pure LLM call (text → text)
  2. summarize_file(t_path, s_path)    → file op (read transcript, write summary)
  3. index_summary(user_id, session_id, s_path, client)
                                       → embed + upsert SessionSummary via the client

An orchestrator `process_unsummarized()` scans the user's session directory
for transcripts missing a summary and delegates to the layers above.

Persona-side: persona never touches silicon_brain ORM. The vector retrieval
also goes through the client (`/api/retrieval/past-summaries`).
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import List, Optional

import httpx

from infra.config import settings
from infra.contracts import SessionSummaryUpsertDTO, SummaryDTO
from infra.model.llm import generate
from persona.teacher.session.transcriber import DATA_DIR
from persona.teacher.silicon_brain_client import SiliconBrainClient


# Path to the skill prompt — the sole source of instructions for the LLM
_SKILL_PROMPT_PATH = Path(__file__).resolve().parent.parent / "skills" / "summarize_session.md"

# Local Ollama embedding client (infra-side; persona doesn't talk to silicon_brain for this).
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
    """Pure transformation: transcript text → summary markdown."""
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
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    summary_path: Path,
    client: SiliconBrainClient,
) -> SummaryDTO:
    """Embed the summary file, extract labels, upsert via the silicon_brain client."""
    summary_text = summary_path.read_text(encoding="utf-8")
    embedding = await _embed_text(summary_text)

    labels: list[str] = []
    labels_match = re.search(r"\*\*Labels\*\*:\s*(.+)", summary_text[:600], re.IGNORECASE)
    if labels_match:
        labels = [l.strip() for l in labels_match.group(1).split(",") if l.strip()]

    return await client.upsert_session_summary(
        user_id,
        SessionSummaryUpsertDTO(
            session_id=session_id,
            file_path=str(summary_path),
            labels=labels,
            embedding=embedding,
        ),
    )


# -------- Orchestrator --------

async def process_unsummarized(user_id: uuid.UUID, client: SiliconBrainClient) -> list[str]:
    """Find transcripts without a summary and process each one. Idempotent."""
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
            await index_summary(user_id, session_id, s_path, client)
            summarized.append(str(session_id))
            print(f"[summarizer] Summarized session {session_id} → {s_path}", flush=True)
        except Exception as e:
            print(f"[summarizer] Failed to summarize session {session_id}: {e}", flush=True)

    return summarized


# -------- Retrieval --------

async def search_past_summaries(
    user_id: uuid.UUID,
    query_embedding: List[float],
    client: SiliconBrainClient,
    *,
    top_k: int = 3,
) -> list[SummaryDTO]:
    """Search past session summaries via the silicon_brain client."""
    return await client.search_past_summaries(user_id, query_embedding, top_k=top_k)
