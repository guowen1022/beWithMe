"""Concept node operations: parse from answers, upsert with HLR, query."""
import re
import uuid
from typing import List
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.knowledge.models import ConceptNode
from app.knowledge.hlr import INITIAL_HALF_LIFE, compute_mastery, update_half_life, mastery_to_state


def parse_concepts(answer: str) -> List[str]:
    """Parse the CONCEPTS: line appended by the model at the end of its answer."""
    match = re.search(r'CONCEPTS:\s*(.+?)$', answer, re.MULTILINE | re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1).strip()
    concepts = [c.strip().lower().strip('.-"\'*') for c in raw.split(",")]
    skip = {"example", "comparison", "explanation", "understanding", "knowledge", "concept", ""}
    return [c for c in concepts if 2 < len(c) < 50 and c not in skip][:5]


async def upsert_concepts(
    db: AsyncSession,
    user_id: uuid.UUID,
    concept_names: List[str],
    demonstrated_understanding: bool = True,
) -> List[ConceptNode]:
    """Create or update concept nodes using half-life regression."""
    now = datetime.utcnow()
    nodes = []
    for name in concept_names:
        result = await db.execute(
            select(ConceptNode).where(ConceptNode.user_id == user_id, ConceptNode.name == name)
        )
        node = result.scalar_one_or_none()

        if node:
            node.encounter_count += 1
            node.last_seen = now
            node.half_life_hours = update_half_life(
                node.half_life_hours, recalled=demonstrated_understanding
            )
            if demonstrated_understanding:
                node.last_recalled_at = now
            ref_time = node.last_recalled_at or node.last_seen
            if ref_time and ref_time.tzinfo is not None:
                ref_time = ref_time.replace(tzinfo=None)
            hours_since = max(0, (now - ref_time).total_seconds() / 3600.0)
            node.state = mastery_to_state(compute_mastery(node.half_life_hours, hours_since))
        else:
            node = ConceptNode(
                user_id=user_id,
                name=name,
                state="learning" if demonstrated_understanding else "faded",
                encounter_count=1,
                half_life_hours=INITIAL_HALF_LIFE,
                last_recalled_at=now if demonstrated_understanding else None,
                first_seen=now,
                last_seen=now,
            )
            db.add(node)

        nodes.append(node)

    await db.commit()
    return nodes


async def get_concepts(
    db: AsyncSession,
    user_id: uuid.UUID,
    state: str = None,
    limit: int = 30,
) -> List[ConceptNode]:
    """Fetch concept nodes, optionally filtered by state."""
    stmt = select(ConceptNode).where(ConceptNode.user_id == user_id).order_by(ConceptNode.last_seen.desc()).limit(limit)
    if state:
        stmt = stmt.where(ConceptNode.state == state)
    result = await db.execute(stmt)
    return list(result.scalars().all())
