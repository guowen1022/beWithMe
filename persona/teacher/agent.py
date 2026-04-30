"""Teacher agent context assembly — reads the silicon brain over HTTP and builds the LLM prompt.

The teacher's core loop:
  1. Read learner state via the silicon_brain HTTP client (no DB session).
  2. Build a personalized prompt.
  3. Return a TeacherContext ready for the LLM.

Persona never touches silicon_brain ORM directly. Every read goes through
`SiliconBrainClient`. Type hints from silicon_brain are TYPE_CHECKING-guarded
in `prompt.py` / `prompt_v2.py`; at runtime the prompt builders accept
duck-typed DTOs from `infra.contracts`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List

from infra.rag.embedding import embed_text

from persona.teacher.prompt import PromptParts, build_answer_prompt, build_history_messages
from persona.teacher.prompt_v2 import build_answer_prompt as build_answer_prompt_v2
from persona.teacher.schemas import AskRequest
from persona.teacher.silicon_brain_client import SiliconBrainClient


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


async def assemble_context(
    body: AskRequest,
    user_id: uuid.UUID,
    client: SiliconBrainClient,
) -> TeacherContext:
    """Read the silicon brain (over HTTP) and assemble the teacher's context."""
    # 1. Brain state — composite read in one call: profile + preferences + concepts + graph_context.
    brain = await client.get_brain_state(
        user_id, session_id=body.session_id, concept_limit=30
    )
    self_description = brain.self_description
    user_profile = brain.profile
    concept_nodes = brain.concept_nodes
    graph_ctx = brain.graph_context

    # 2. Embed the query (infra-side, no silicon_brain involvement).
    embed_context = body.selected_text or body.passage_text or ""
    query_text = (embed_context + " " + body.question) if embed_context else body.question
    try:
        query_embedding = await embed_text(query_text)
    except Exception:
        query_embedding = None

    # 3. Boost the query with the user's preference embedding (if any).
    if query_embedding:
        try:
            query_embedding = await client.boost_embedding(user_id, query_embedding)
        except Exception as e:
            print(f"[teacher] boost embedding error: {e}", flush=True)

    # 4. Retrieve relevant document chunks (vector search via knowledge sidecar).
    doc_chunks: list = []
    if body.document_id and query_embedding:
        try:
            doc_chunks = await client.search_document_chunks(
                user_id, body.document_id, query_embedding, top_k=5
            )
        except Exception as e:
            print(f"[teacher] doc-chunk search error: {e}", flush=True)

    # 5. Past session summaries (vector search over session_summaries).
    if query_embedding:
        try:
            past_summaries = await client.search_past_summaries(user_id, query_embedding, top_k=2)
            if past_summaries:
                summary_lines = ["RELEVANT PAST LEARNING SESSIONS:"]
                for s in past_summaries:
                    if s.content:
                        summary_lines.append(f"---\n{s.content}\n---")
                past_ctx = "\n".join(summary_lines)
                graph_ctx = f"{graph_ctx}\n\n{past_ctx}" if graph_ctx else past_ctx
        except Exception as e:
            print(f"[teacher] past summary search error: {e}", flush=True)

    # 6. Session history → multi-turn messages.
    prior_interactions = await client.get_session_history(user_id, body.session_id)
    prior_messages = build_history_messages(prior_interactions)

    # 7. Build the prompt.
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
