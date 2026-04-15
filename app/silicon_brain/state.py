"""Brain state facade — assembles the full user snapshot for any agent."""

import uuid
from typing import Optional, List
from dataclasses import dataclass, field
from sqlalchemy.ext.asyncio import AsyncSession

from app.silicon_brain.user_profile import get_user_profile, UserProfileState, boost_query_embedding
from app.silicon_brain.knowledge import get_concepts, get_graph_context, ConceptNode


@dataclass
class BrainState:
    """Everything an agent needs to understand the learner."""
    # Who the user is
    self_description: str = ""
    # How the user learns
    profile: Optional[UserProfileState] = None
    # What the user knows
    concept_nodes: List[ConceptNode] = field(default_factory=list)
    # How concepts relate
    graph_context: str = ""


async def get_brain_state(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: Optional[uuid.UUID] = None,
    concept_limit: int = 30,
) -> BrainState:
    """Assemble the full brain state for any agent to consume."""
    from app.silicon_brain.models.profile import Profile
    from sqlalchemy import select

    # Self-description
    result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one_or_none()
    self_description = profile.self_description if profile else ""

    # User profile (preferences + embedding + session signals)
    user_profile = await get_user_profile(db, user_id, session_id=session_id)

    # Concept mastery snapshot
    concept_nodes = await get_concepts(db, user_id, limit=concept_limit)

    # Graph context (related concept neighborhoods)
    graph_ctx = ""
    if concept_nodes:
        try:
            concept_names = [c.name for c in concept_nodes[:10]]
            graph_ctx = await get_graph_context(db, user_id, concept_names)
        except Exception as e:
            print(f"[silicon_brain] graph walk error: {e}", flush=True)

    return BrainState(
        self_description=self_description,
        profile=user_profile,
        concept_nodes=concept_nodes,
        graph_context=graph_ctx,
    )
