"""Generic ingestion pipeline — any agent feeds learnings through here.

The brain builder doesn't care where input comes from. It just knows how
to turn observations into knowledge in the silicon brain.
"""

import uuid
from dataclasses import dataclass, field
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.silicon_brain.services.embedding import embed_text
from app.brain_builder.concept_builder import build_concepts
from app.brain_builder.preference_builder import update_preference_embedding, maybe_distill


@dataclass
class AgentLearning:
    """Structured input from any agent to the brain builder."""
    source: str                          # "teacher", "calendar", "helper", etc.
    user_id: uuid.UUID
    session_id: uuid.UUID
    # What happened
    text_to_embed: str                   # text for embedding (e.g. passage + question)
    answer_text: str                     # agent's output (parsed for CONCEPTS: line)
    context: str = ""                    # surrounding context for edge creation
    # Optional overrides
    concepts: Optional[List[str]] = None  # if agent already extracted concepts
    embedding: Optional[List[float]] = None  # if already embedded
    metadata: dict = field(default_factory=dict)


async def process_learning(db: AsyncSession, learning: AgentLearning) -> dict:
    """Process a learning from any agent and update the silicon brain.

    Returns a summary of what was updated.
    """
    result = {"source": learning.source, "embedded": False, "concepts": [], "distilled": False}

    # 1. Embed the interaction (if not pre-computed)
    embedding = learning.embedding
    if embedding is None:
        try:
            embedding = await embed_text(learning.text_to_embed)
            result["embedded"] = True
        except Exception as e:
            print(f"[brain_builder] Failed to embed: {e}", flush=True)
            return result

    # 2. EMA-update the preference embedding
    if embedding:
        await update_preference_embedding(db, learning.user_id, embedding)

    # 3. Extract and upsert concepts
    concepts = learning.concepts
    if concepts is None:
        concepts = await build_concepts(
            db, learning.user_id, learning.answer_text, context=learning.context,
        )
    result["concepts"] = concepts

    # 4. Auto-distill preferences if threshold met
    await maybe_distill(db, learning.user_id)

    print(f"[brain_builder] Processed learning from {learning.source}: "
          f"embedded={result['embedded']}, concepts={concepts}", flush=True)

    return {**result, "embedding": embedding}
