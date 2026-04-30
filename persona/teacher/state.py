"""Teacher's view of the user — composite of silicon_brain reads + teacher's own DB.

`TeacherView` is what the teacher sees about a learner: the user's
self-description (read from silicon_brain via the client by the caller and
passed in), plus teacher's own internal data (concept mastery, graph
context, preferences distillation) read directly from teacher's tables.

This module never imports silicon_brain models. Cross-domain user data
arrives as parameters — caller is responsible for HTTP-fetching it.
"""
import uuid
from typing import Optional, List
from dataclasses import dataclass, field
from sqlalchemy.ext.asyncio import AsyncSession

from persona.teacher.preferences import get_user_profile, UserProfileState
from persona.teacher.knowledge import get_concepts, get_graph_context, ConceptNode


@dataclass
class TeacherView:
    """Everything the teacher needs to understand the learner — for itself."""
    self_description: str = ""               # provided by caller (from silicon_brain)
    profile: Optional[UserProfileState] = None   # teacher's preference state
    concept_nodes: List[ConceptNode] = field(default_factory=list)
    graph_context: str = ""


# Backwards-compatible alias during migration. Callers that previously
# imported `BrainState` get the same dataclass.
BrainState = TeacherView


async def get_teacher_view(
    db: AsyncSession,
    user_id: uuid.UUID,
    self_description: str,
    *,
    session_id: Optional[uuid.UUID] = None,
    concept_limit: int = 30,
) -> TeacherView:
    """Compose the teacher's view. Caller passes in the user's self_description
    (already fetched from silicon_brain via the client).
    """
    user_profile = await get_user_profile(db, user_id, session_id=session_id)
    concept_nodes = await get_concepts(db, user_id, limit=concept_limit)

    graph_ctx = ""
    if concept_nodes:
        try:
            concept_names = [c.name for c in concept_nodes[:10]]
            graph_ctx = await get_graph_context(db, user_id, concept_names)
        except Exception as e:
            print(f"[teacher.state] graph walk error: {e}", flush=True)

    return TeacherView(
        self_description=self_description,
        profile=user_profile,
        concept_nodes=concept_nodes,
        graph_context=graph_ctx,
    )


# Backwards-compatible alias during migration.
get_brain_state = get_teacher_view
