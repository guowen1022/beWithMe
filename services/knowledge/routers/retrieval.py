"""Vector-search HTTP APIs persona consumes."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts import DocumentChunkDTO, SummaryDTO
from silicon_brain.db import get_db
from silicon_brain.retrieval import search_document_chunks


router = APIRouter()


class _DocChunksRequest(BaseModel):
    document_id: UUID
    query_embedding: list[float]
    top_k: int = 5


@router.post("/retrieval/document-chunks", response_model=list[DocumentChunkDTO])
async def retrieve_document_chunks(
    body: _DocChunksRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    chunks = await search_document_chunks(db, body.document_id, body.query_embedding, top_k=body.top_k)
    return [DocumentChunkDTO.model_validate(c) for c in chunks]


class _PastSummariesRequest(BaseModel):
    query_embedding: list[float]
    top_k: int = 3


@router.post("/retrieval/past-summaries", response_model=list[SummaryDTO])
async def retrieve_past_summaries(
    body: _PastSummariesRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(parse_user_id),
):
    """Mirror of persona/teacher/session/summarizer.search_past_summaries.

    Vector search over `session_summaries` then read the on-disk summary file.
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
        "embedding": str(body.query_embedding),
        "limit": body.top_k,
    })
    rows = result.fetchall()

    out: list[SummaryDTO] = []
    for session_id, file_path, distance in rows:
        path = Path(file_path)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        out.append(SummaryDTO(
            session_id=session_id,
            file_path=file_path,
            similarity=1.0 - float(distance),
            content=content,
        ))
    return out
