"""Teacher's vector search — over teacher's own Interactions.

Document chunk search lives in silicon_brain side (the knowledge sidecar's
retrieval router) because Documents are user data, not teacher's. Teacher
calls the narrow `SiliconBrainClient.search_document_chunks` to get them.
"""
from typing import List
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from persona.teacher.models.interaction import Interaction


async def search_similar_interactions(
    db: AsyncSession, user_id: UUID, query_embedding: List[float], top_k: int = 5
) -> List[Interaction]:
    stmt = text("""
        SELECT id, user_id, session_id, passage_text, question, answer, source_document, metadata, created_at
        FROM interactions
        WHERE user_id = :user_id AND embedding IS NOT NULL
        ORDER BY embedding <=> :embedding
        LIMIT :limit
    """)
    result = await db.execute(stmt, {"user_id": str(user_id), "embedding": str(query_embedding), "limit": top_k})
    rows = result.fetchall()
    interactions = []
    for row in rows:
        i = Interaction()
        i.id, i.user_id, i.session_id, i.passage_text, i.question, i.answer, i.source_document, i.metadata_, i.created_at = row
        interactions.append(i)
    return interactions
