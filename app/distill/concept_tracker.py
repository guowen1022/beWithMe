"""Extract concepts from interactions and maintain the concept graph with HLR."""

import json
import re
from typing import List
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.distill.models import ConceptNode
from app.distill.hlr import (
    INITIAL_HALF_LIFE,
    compute_mastery,
    update_half_life,
    mastery_to_state,
)
from app.services.llm import generate_json

EXTRACT_PROMPT = """Extract DOMAIN KNOWLEDGE concepts from this reading interaction.

Passage: {passage}
Question: {question}
Answer: {answer}

Rules:
- Return 1-5 DOMAIN CONCEPTS only — things you'd find in a textbook or encyclopedia
- Good examples: "penicillin", "self-attention", "gradient descent", "antibiotic resistance", "transformer architecture"
- Do NOT include: generic words ("example", "comparison"), method/style terms ("item similarity", "analogy", "formal explanation"), meta-learning terms ("understanding", "knowledge"), or vague phrases
- Each concept should be a specific, teachable topic that belongs in a knowledge graph

Return ONLY a JSON array like: ["concept1", "concept2", ...]"""


async def extract_concepts(
    passage: str, question: str, answer: str
) -> List[str]:
    """Extract concept names from an interaction via LLM."""
    prompt = EXTRACT_PROMPT.format(
        passage=(passage or "")[:500],
        question=question,
        answer=answer[:500],
    )
    # Try up to 2 times (model sometimes produces only thinking blocks)
    raw = ""
    for attempt in range(2):
        raw = await generate_json(prompt, max_tokens=256)
        if raw:
            break
        print(f"[concept_tracker] Empty response, attempt {attempt + 1}", flush=True)

    if not raw:
        print(f"[concept_tracker] No text output after retries", flush=True)
        return []

    # Try multiple extraction strategies
    try:
        match = re.search(r'\[[\s]*"[^"]*"[\s,]*(?:"[^"]*"[\s,]*)*\]', raw, re.DOTALL)
        if match:
            concepts = json.loads(match.group())
            result = [c.lower().strip() for c in concepts if isinstance(c, str) and c.strip()]
            if result:
                return result
    except (json.JSONDecodeError, AttributeError):
        pass

    try:
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            concepts = json.loads(match.group())
            result = [c.lower().strip() for c in concepts if isinstance(c, str) and c.strip()]
            if result:
                return result
    except (json.JSONDecodeError, AttributeError):
        pass

    print(f"[concept_tracker] Failed to parse: {raw[:100]}")
    return []


async def upsert_concepts_hlr(
    db: AsyncSession,
    concept_names: List[str],
    demonstrated_understanding: bool = True,
) -> List[ConceptNode]:
    """Create or update concept nodes using half-life regression.

    When demonstrated_understanding is True, the half-life is doubled
    (stronger memory). Otherwise it is halved (weaker/forgotten).
    """
    now = datetime.utcnow()
    nodes = []
    for name in concept_names:
        result = await db.execute(
            select(ConceptNode).where(ConceptNode.name == name)
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

            # Compute live mastery from HLR
            ref_time = node.last_recalled_at or node.last_seen
            hours_since = (now - ref_time).total_seconds() / 3600.0
            p = compute_mastery(node.half_life_hours, hours_since)
            node.state = mastery_to_state(p)
        else:
            node = ConceptNode(
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
