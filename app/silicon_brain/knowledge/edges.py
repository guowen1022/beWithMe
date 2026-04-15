"""Edge operations: create, reinforce, and decay concept edges."""
import uuid
from typing import List, Optional
from datetime import datetime
from itertools import combinations
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.silicon_brain.knowledge.models import ConceptNode, ConceptEdge
from app.silicon_brain.knowledge.hlr import DECAY_HALF_LIFE_DAYS


async def link_concepts(
    db: AsyncSession,
    user_id: uuid.UUID,
    concept_names: List[str],
    edge_type: str = "temporal",
    context: Optional[str] = None,
) -> List[ConceptEdge]:
    """Create or reinforce edges between all pairs of given concepts."""
    if len(concept_names) < 2:
        return []

    result = await db.execute(
        select(ConceptNode).where(ConceptNode.user_id == user_id, ConceptNode.name.in_(concept_names))
    )
    nodes = {n.name: n for n in result.scalars().all()}

    edges = []
    now = datetime.utcnow()
    for a, b in combinations(concept_names, 2):
        if a not in nodes or b not in nodes:
            continue
        src, tgt = sorted([nodes[a], nodes[b]], key=lambda n: str(n.id))

        existing = await db.execute(
            select(ConceptEdge).where(
                ConceptEdge.source_id == src.id,
                ConceptEdge.target_id == tgt.id,
                ConceptEdge.edge_type == edge_type,
            )
        )
        edge = existing.scalar_one_or_none()

        if edge:
            edge.weight = min(edge.weight + 0.5, 10.0)
            edge.last_reinforced = now
        else:
            edge = ConceptEdge(
                user_id=user_id,
                source_id=src.id,
                target_id=tgt.id,
                edge_type=edge_type,
                weight=1.0,
                context=context,
            )
            db.add(edge)
        edges.append(edge)

    await db.commit()
    return edges


async def decay_edges(db: AsyncSession, user_id: uuid.UUID, half_life_days: float = DECAY_HALF_LIFE_DAYS):
    """Decay edge weights and prune edges below threshold.

    weight = weight * 0.5^(days_since_reinforced / half_life_days)
    Edges below 0.1 are deleted.
    """
    now = datetime.utcnow()
    result = await db.execute(select(ConceptEdge).where(ConceptEdge.user_id == user_id))
    pruned = 0
    decayed = 0

    for edge in result.scalars().all():
        reinforced = edge.last_reinforced
        if reinforced and reinforced.tzinfo is not None:
            reinforced = reinforced.replace(tzinfo=None)
        days_since = (now - reinforced).total_seconds() / 86400.0
        new_weight = edge.weight * (0.5 ** (days_since / half_life_days))

        if new_weight < 0.1:
            await db.delete(edge)
            pruned += 1
        else:
            edge.weight = round(new_weight, 3)
            decayed += 1

    await db.commit()
    print(f"[decay_edges] Decayed {decayed}, pruned {pruned}", flush=True)
