"""Vector search over silicon_brain user data (Documents).

Past-session-summary search has moved to teacher's internal code (the
`session_summaries` table belongs to teacher now). Only document-chunk
search remains here, since DocumentChunks are user uploads.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id
from infra.contracts import DocumentChunkDTO
from infra.db import get_db


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
    stmt = text("""
        SELECT id, document_id, chunk_index, text, page_number
        FROM document_chunks
        WHERE document_id = :doc_id AND embedding IS NOT NULL
        ORDER BY embedding <=> :embedding
        LIMIT :limit
    """)
    result = await db.execute(stmt, {
        "doc_id": str(body.document_id),
        "embedding": str(body.query_embedding),
        "limit": body.top_k,
    })
    rows = result.fetchall()
    return [
        DocumentChunkDTO(
            id=r[0], document_id=r[1], chunk_index=r[2], text=r[3], page_number=r[4],
        )
        for r in rows
    ]
