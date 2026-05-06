"""Recommendation engine — LLM + web search sources.

Reads teacher's brain state directly from teacher's own DB. Persists
Recommendation rows directly to teacher's DB. silicon_brain not involved.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from playwright.async_api import BrowserContext
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from infra.hlr import compute_mastery, mastery_to_state
from infra.model import llm
from infra.tools.web_fetch import fetch_readable, WebFetchError

from persona.teacher.knowledge import get_concepts, get_graph_context
from persona.teacher.models.recommendation import Recommendation
from persona.teacher.preferences import get_user_profile


RECOMMEND_SYSTEM_PROMPT = """\
You are a learning recommendation engine. Given a learner's profile, concept mastery states, \
and knowledge graph context, generate personalized recommendations for what they should study next.

CRITICAL DESIGN PRINCIPLE: Keep every recommendation bite-sized and fractional. \
The goal is small pieces that connect to each other, so users can learn with almost no time commitment.

Categories and their scope:
- "review" (~1 min): ONE concept only. A quick refresher — 5-6 sentences max to jog memory.
- "explore" (~3 min): ONE new concept. A brief introduction — just enough for the user to \
decide if they want to go deeper. 5-6 sentences covering the core idea.
- "deepen" (~5 min): ONE concept the user partially knows. Go one level deeper with a \
concrete example or connection to something they already understand.

If a topic is large (e.g., a whole paper or broad subject), do NOT recommend learning the whole thing. \
Instead, break it into the smallest meaningful piece — one concept, one insight, one connection. \
For a paper with many concepts, recommend a quick overview (~5 min) just to let the user know \
it exists and what it covers, not to learn everything in it.

Output a JSON array of 5-8 recommendations. Each recommendation must have:
- "category": one of "review", "explore", "deepen"
- "title": short, actionable title (e.g., "Review: Gradient Descent Basics")
- "summary": 1-2 sentence explanation of what to study and why
- "reasoning": why this is recommended based on their learning state
- "concept_names": list with 1-2 concept names only (keep it focused)
- "priority": float 0-1 (1 = most urgent)

Prioritize:
1. Concepts with "rusty" or "faded" mastery that connect to many other concepts
2. Gaps in the knowledge graph (areas with few connections)
3. Topics that build on what the learner already knows well

Respond with ONLY the JSON array, no other text."""


def _build_learner_snapshot(
    self_description: str,
    profile,  # UserProfileState
    concept_masteries: list[dict],
    graph_context: str,
) -> str:
    """Build a text snapshot of the learner for the LLM prompt."""
    parts: list[str] = []

    if self_description:
        parts.append(f"## Learner Background\n{self_description}")

    if profile is not None:
        parts.append(
            "## Learning Preferences\n"
            f"- Explanation style: {profile.explanation_style}\n"
            f"- Depth: {profile.depth_preference}\n"
            f"- Analogy affinity: {profile.analogy_affinity}\n"
            f"- Math comfort: {profile.math_comfort}\n"
            f"- Pacing: {profile.pacing}"
        )

    if concept_masteries:
        lines = []
        for cm in concept_masteries:
            lines.append(
                f"- {cm['name']}: {cm['state']} (mastery={cm['mastery']:.2f}, "
                f"encounters={cm['encounters']}, half_life={cm['half_life']:.1f}h)"
            )
        parts.append(f"## Concept Mastery ({len(concept_masteries)} concepts)\n" + "\n".join(lines))

    if graph_context:
        parts.append(f"## Knowledge Graph Context\n{graph_context}")

    return "\n\n".join(parts)


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return raw


async def _replace_active(
    db: AsyncSession,
    user_id: uuid.UUID,
    source: str,
    new_rows: list[Recommendation],
) -> list[Recommendation]:
    """Transactionally delete active recs of `source`, insert new batch."""
    await db.execute(
        delete(Recommendation).where(
            Recommendation.user_id == user_id,
            Recommendation.source == source,
            Recommendation.status == "active",
        )
    )
    for r in new_rows:
        db.add(r)
    await db.commit()
    for r in new_rows:
        await db.refresh(r)
    return new_rows


async def generate_llm_recommendations(
    db: AsyncSession,
    user_id: uuid.UUID,
    self_description: str,
) -> list[Recommendation]:
    """Generate recommendations by having the LLM reason about the user's learning state.

    Replaces active LLM recommendations transactionally.

    `self_description` is read by the caller from silicon_brain via the client
    and passed in (we don't import silicon_brain here).
    """
    profile = await get_user_profile(db, user_id)
    concept_nodes = await get_concepts(db, user_id, limit=50)

    graph_context = ""
    if concept_nodes:
        try:
            graph_context = await get_graph_context(
                db, user_id, [c.name for c in concept_nodes[:10]]
            )
        except Exception as e:
            print(f"[recommender] graph walk error: {e}", flush=True)

    # Compute current mastery for each concept (HLR is stateless infra math).
    now = datetime.now(timezone.utc)
    concept_masteries: list[dict] = []
    for node in concept_nodes:
        ref_time = node.last_recalled_at or node.last_seen
        if ref_time is None:
            hours_since = 0.0
        else:
            if ref_time.tzinfo is None:
                ref_time = ref_time.replace(tzinfo=timezone.utc)
            hours_since = (now - ref_time).total_seconds() / 3600
        mastery = compute_mastery(node.half_life_hours or 0.0, hours_since)
        state = mastery_to_state(mastery)
        concept_masteries.append({
            "name": node.name,
            "mastery": mastery,
            "state": state,
            "encounters": node.encounter_count,
            "half_life": node.half_life_hours or 0.0,
        })

    snapshot = _build_learner_snapshot(self_description, profile, concept_masteries, graph_context)
    prompt = f"Here is the learner's current state:\n\n{snapshot}\n\nGenerate recommendations."

    raw = await llm.generate(
        prompt, system=RECOMMEND_SYSTEM_PROMPT, max_tokens=2048,
        purpose="recommender", user_id=user_id,
    )
    try:
        items = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        print(f"[recommender] Failed to parse LLM response as JSON: {raw[:200]}", flush=True)
        return []

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    rows = [
        Recommendation(
            user_id=user_id,
            source="llm",
            category=item.get("category", "explore"),
            title=item.get("title", ""),
            summary=item.get("summary", ""),
            reasoning=item.get("reasoning", ""),
            concept_names=item.get("concept_names", []),
            priority=float(item.get("priority", 0.5)),
            status="active",
            expires_at=expires_at,
        )
        for item in items
    ]
    return await _replace_active(db, user_id, "llm", rows)


async def generate_web_recommendations(
    db: AsyncSession,
    user_id: uuid.UUID,
    browser_context: BrowserContext,
    llm_recommendations: Optional[list[Recommendation]] = None,
) -> list[Recommendation]:
    """Search the web for content relevant to the user's learning gaps.

    Replaces active web recommendations transactionally.
    """
    search_queries: list[str] = []
    if llm_recommendations:
        for rec in llm_recommendations:
            if rec.category == "article":
                if rec.concept_names:
                    search_queries.append(" ".join(rec.concept_names[:3]) + " tutorial explanation")
                elif rec.title:
                    search_queries.append(rec.title)

    if not search_queries and llm_recommendations:
        for rec in llm_recommendations:
            if rec.category == "review" and rec.concept_names:
                search_queries.append(rec.concept_names[0] + " beginner guide")
                if len(search_queries) >= 3:
                    break

    if not search_queries:
        return []

    expires_at = datetime.now(timezone.utc) + timedelta(days=3)
    new_rows: list[Recommendation] = []

    for query in search_queries[:3]:
        search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        try:
            title, text, _ = await fetch_readable(search_url, browser_context)
        except WebFetchError as e:
            print(f"[recommender] Web fetch failed for '{query}': {e}", flush=True)
            continue

        eval_prompt = (
            f"You found this web content while searching for '{query}':\n\n"
            f"Title: {title}\n"
            f"Content (first 2000 chars): {text[:2000]}\n\n"
            f"Is this content useful for a learner studying these topics? "
            f"Respond with JSON: {{\"useful\": true/false, \"title\": \"...\", "
            f"\"summary\": \"1-2 sentence summary\", \"priority\": 0.0-1.0}}"
        )
        eval_raw = await llm.generate(eval_prompt, max_tokens=256)
        try:
            evaluation = json.loads(_strip_code_fence(eval_raw))
        except json.JSONDecodeError:
            continue

        if not evaluation.get("useful", False):
            continue

        new_rows.append(Recommendation(
            user_id=user_id,
            source="web",
            category="article",
            title=evaluation.get("title", title),
            summary=evaluation.get("summary", ""),
            reasoning=f"Found via search for: {query}",
            concept_names=query.split()[:3],
            priority=float(evaluation.get("priority", 0.4)),
            status="active",
            expires_at=expires_at,
            url=search_url,
        ))

    return await _replace_active(db, user_id, "web", new_rows)
