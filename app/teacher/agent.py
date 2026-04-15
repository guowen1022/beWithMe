"""Teacher agent context assembly — reads the silicon brain and builds the LLM prompt.

This is the teacher's core loop:
  1. Read learner state from the silicon brain
  2. Build a personalized prompt
  3. Return a TeacherContext ready for the LLM
"""

import uuid
from dataclasses import dataclass
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.silicon_brain.models.interaction import Interaction
from app.silicon_brain.models.profile import Profile
from app.teacher.schemas import AskRequest
from app.silicon_brain.services.embedding import embed_text
from app.silicon_brain.services.retrieval import search_document_chunks
from app.silicon_brain.user_profile import get_user_profile, boost_query_embedding
from app.silicon_brain.knowledge import get_graph_context, get_concepts
from app.teacher.prompt import PromptParts, build_answer_prompt, build_history_messages
from app.teacher.skills.v2_prompt import build_answer_prompt as build_answer_prompt_v2

# Registry: maps version string -> builder callable
_PROMPT_BUILDERS = {
    "v1": build_answer_prompt,
    "v2": build_answer_prompt_v2,
}


@dataclass
class TeacherContext:
    """Everything the teacher needs to generate an answer."""
    parts: PromptParts
    prior_messages: List[dict]


async def fetch_session_history(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
) -> list[Interaction]:
    """All prior interactions in this session, in chronological order.

    The recursive-question feature treats every Q&A in a session as one
    flat conversation from the model's perspective, regardless of how the
    user navigated the tree in the UI. This is the source for the messages
    array sent to the LLM.
    """
    stmt = (
        select(Interaction)
        .where(Interaction.user_id == user_id, Interaction.session_id == session_id)
        .order_by(Interaction.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def assemble_context(
    body: AskRequest, db: AsyncSession, user_id: uuid.UUID
) -> TeacherContext:
    """Read the silicon brain and assemble the teacher's context."""
    # Self-description
    result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one_or_none()
    self_description = profile.self_description if profile else ""

    # Embed the query
    embed_context = body.selected_text or body.passage_text or ""
    query_text = (embed_context + " " + body.question) if embed_context else body.question
    try:
        query_embedding = await embed_text(query_text)
    except Exception:
        query_embedding = None

    # Boost query with user's preference embedding for personalized retrieval
    if query_embedding:
        query_embedding = await boost_query_embedding(db, user_id, query_embedding)

    # Retrieve relevant document chunks
    doc_chunks = []
    if body.document_id and query_embedding:
        doc_chunks = await search_document_chunks(db, body.document_id, query_embedding, top_k=5)

    # Read the silicon brain
    user_profile = await get_user_profile(db, user_id, session_id=body.session_id)
    concept_nodes = await get_concepts(db, user_id, limit=30)

    graph_ctx = ""
    if concept_nodes:
        try:
            concept_names = [c.name for c in concept_nodes[:10]]
            graph_ctx = await get_graph_context(db, user_id, concept_names)
        except Exception as e:
            print(f"[teacher] graph walk error: {e}", flush=True)

    # Session history → multi-turn messages
    prior_interactions = await fetch_session_history(db, user_id, body.session_id)
    prior_messages = build_history_messages(prior_interactions)

    # Build the prompt — route to the selected version's builder
    builder = _PROMPT_BUILDERS[body.prompt_version]
    parts = builder(
        passage=body.passage_text,
        selected_text=body.selected_text,
        question=body.question,
        self_description=self_description,
        doc_chunks=doc_chunks,
        user_profile=user_profile,
        concept_nodes=concept_nodes,
        graph_context=graph_ctx,
    )

    return TeacherContext(parts=parts, prior_messages=prior_messages)
