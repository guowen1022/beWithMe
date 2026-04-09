from typing import List
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.interaction import Interaction
from app.models.document import DocumentChunk


async def search_similar_interactions(
    db: AsyncSession, query_embedding: List[float], top_k: int = 5
) -> List[Interaction]:
    stmt = text("""
        SELECT id, session_id, passage_text, question, answer, source_document, metadata, created_at
        FROM interactions
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> :embedding
        LIMIT :limit
    """)
    result = await db.execute(stmt, {"embedding": str(query_embedding), "limit": top_k})
    rows = result.fetchall()
    interactions = []
    for row in rows:
        i = Interaction()
        i.id, i.session_id, i.passage_text, i.question, i.answer, i.source_document, i.metadata_, i.created_at = row
        interactions.append(i)
    return interactions


async def search_document_chunks(
    db: AsyncSession, document_id: UUID, query_embedding: List[float], top_k: int = 5
) -> List[DocumentChunk]:
    stmt = text("""
        SELECT id, document_id, chunk_index, text, created_at
        FROM document_chunks
        WHERE document_id = :doc_id AND embedding IS NOT NULL
        ORDER BY embedding <=> :embedding
        LIMIT :limit
    """)
    result = await db.execute(
        stmt, {"doc_id": str(document_id), "embedding": str(query_embedding), "limit": top_k}
    )
    rows = result.fetchall()
    chunks = []
    for row in rows:
        c = DocumentChunk()
        c.id, c.document_id, c.chunk_index, c.text, c.created_at = row
        chunks.append(c)
    return chunks
