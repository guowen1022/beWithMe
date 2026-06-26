"""Answer-scenario context assembly — the heavy pipeline (orchestrator).

Reads from silicon_brain (HTTP) + the teacher's own DB and assembles
everything `prompts.answer.build` needs: profile, preferences, concepts,
graph context, embedded query, doc-chunk RAG, past summaries, session
history, and the live canvas state.

This file is the **orchestrator**: it calls the per-phase helpers in
`_answer_parts.py` in order. Each helper owns one phase (and its own latency
timing); the sequence below is the data flow at a glance. Behavior is
identical to the original inline pipeline.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from infra.silicon_brain_client import SiliconBrainClient
from persona.teacher.prompts.parts import PromptParts
from persona.teacher.schemas import AskRequest
from persona.teacher.contexts import _answer_parts as parts


@dataclass
class TeacherContext:
    """Everything the teacher needs to generate an answer."""
    parts: PromptParts
    prior_messages: List[dict]


async def assemble(
    body: AskRequest,
    user_id: uuid.UUID,
    db: AsyncSession,
    client: SiliconBrainClient,
    phases: Optional[dict] = None,
    voice_mode: bool = False,
    lead_pass: bool = False,
) -> TeacherContext:
    """Read silicon_brain (HTTP) + teacher's own DB and build the
    answer-scenario prompt. The full RAG + history pipeline, as an ordered
    sequence of phase helpers.

    If `phases` is provided, the dict is populated with per-step elapsed
    milliseconds keyed by `ctx_<step>_ms` (benchmark instrumentation).

    Prompt builder dispatch:
      lead_pass=True    → lead_brief (fast front-line spoken answer; deeper
                          work handled by a separate pass).
      voice_mode=True   → voice_answer (single-turn voice, full tool palette).
      otherwise         → answer (text mode).
    """
    # --- User context ---
    self_description = await parts.load_self_description(client, user_id, phases)
    talk_preference = await parts.load_talk_preference(client, user_id, phases)
    user_profile = await parts.load_user_profile(db, user_id, body.session_id, phases)
    concept_nodes, graph_ctx = await parts.load_concepts_and_graph(db, user_id, phases)

    # --- Embed + retrieve ---
    query_embedding = await parts.embed_query(db, user_id, body, phases)
    doc_chunks = await parts.retrieve_doc_chunks(client, user_id, body, query_embedding, phases)
    graph_ctx = await parts.merge_past_summaries(db, user_id, query_embedding, graph_ctx, phases)

    # --- History + live canvas ---
    prior_messages = await parts.load_session_history(db, user_id, body.session_id, phases)
    canvas_state = await parts.read_canvas_state(user_id, body, phases)

    # --- Produced-materials inventory (lead pass only) ---
    # A titles-only list of notes the teacher has drawn, read from the durable
    # note store — so the fast line can name "your LRU diagram" and route deep
    # instead of disclaiming. Skipped on non-lead paths (they have tools).
    produced_inventory = ""
    if lead_pass:
        try:
            from persona.teacher.contexts._produced_notes import (
                collect_produced_notes, render_inventory,
            )
            produced_inventory = render_inventory(
                collect_produced_notes(user_id, limit=5, max_age_s=6 * 3600)
            )
        except Exception as e:
            print(f"[teacher.context] produced-notes inventory failed: {e}", flush=True)

    # --- Build prompt (+ optional maestro overlay) ---
    prompt_parts = parts.build_prompt(
        body, lead_pass, voice_mode,
        self_description=self_description,
        doc_chunks=doc_chunks,
        user_profile=user_profile,
        concept_nodes=concept_nodes,
        graph_context=graph_ctx,
        canvas_state=canvas_state,
        talk_preference=talk_preference,
        phases=phases,
        produced_inventory=produced_inventory,
    )
    prompt_parts = await parts.apply_maestro_frame(user_id, prompt_parts)

    return TeacherContext(parts=prompt_parts, prior_messages=prior_messages)


__all__ = ["TeacherContext", "assemble"]
