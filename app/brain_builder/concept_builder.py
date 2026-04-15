"""Concept building: extract concepts from text, upsert with HLR, create edges."""

import uuid
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.silicon_brain.knowledge import parse_concepts, upsert_concepts, link_concepts


async def build_concepts(
    db: AsyncSession,
    user_id: uuid.UUID,
    answer_text: str,
    context: str = "",
) -> List[str]:
    """Parse concepts from an agent's output and update the knowledge graph.

    Returns the list of concept names that were extracted.
    """
    concepts = parse_concepts(answer_text)
    if not concepts:
        return []

    nodes = await upsert_concepts(db, user_id, concepts)
    print(f"[brain_builder] Concepts: {[n.name for n in nodes]}", flush=True)

    if len(concepts) >= 2:
        edges = await link_concepts(db, user_id, concepts, context=context[:100])
        print(f"[brain_builder] Edges: {len(edges)}", flush=True)

    return concepts
