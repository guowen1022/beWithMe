"""Recommendation engine — LLM + web search sources."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from playwright.async_api import BrowserContext

from app.teacher.recommender.models import Recommendation
from app.silicon_brain.state import get_brain_state
from app.silicon_brain.knowledge.hlr import compute_mastery, mastery_to_state
from app.infra.model import llm
from app.infra.tools.web_fetch import fetch_readable, WebFetchError

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


def _build_learner_snapshot(brain_state, concept_masteries: list[dict]) -> str:
    """Build a text snapshot of the learner for the LLM prompt."""
    parts = []

    if brain_state.self_description:
        parts.append(f"## Learner Background\n{brain_state.self_description}")

    if brain_state.profile:
        p = brain_state.profile
        parts.append(f"## Learning Preferences\n"
                     f"- Explanation style: {p.explanation_style}\n"
                     f"- Depth: {p.depth_preference}\n"
                     f"- Analogy affinity: {p.analogy_affinity}\n"
                     f"- Math comfort: {p.math_comfort}\n"
                     f"- Pacing: {p.pacing}")

    if concept_masteries:
        lines = []
        for cm in concept_masteries:
            lines.append(f"- {cm['name']}: {cm['state']} (mastery={cm['mastery']:.2f}, "
                         f"encounters={cm['encounters']}, half_life={cm['half_life']:.1f}h)")
        parts.append(f"## Concept Mastery ({len(concept_masteries)} concepts)\n" + "\n".join(lines))

    if brain_state.graph_context:
        parts.append(f"## Knowledge Graph Context\n{brain_state.graph_context}")

    return "\n\n".join(parts)


async def generate_llm_recommendations(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[Recommendation]:
    """Generate recommendations by having the LLM reason about the user's learning state."""
    brain = await get_brain_state(db, user_id, concept_limit=50)

    # Compute current mastery for each concept
    now = datetime.now(timezone.utc)
    concept_masteries = []
    for node in brain.concept_nodes:
        if node.last_recalled_at:
            hours_since = (now - node.last_recalled_at).total_seconds() / 3600
        else:
            hours_since = (now - node.last_seen).total_seconds() / 3600
        mastery = compute_mastery(node.half_life_hours, hours_since)
        state = mastery_to_state(mastery)
        concept_masteries.append({
            "name": node.name,
            "mastery": mastery,
            "state": state,
            "encounters": node.encounter_count,
            "half_life": node.half_life_hours,
        })

    snapshot = _build_learner_snapshot(brain, concept_masteries)
    prompt = f"Here is the learner's current state:\n\n{snapshot}\n\nGenerate recommendations."

    raw = await llm.generate(prompt, system=RECOMMEND_SYSTEM_PROMPT, max_tokens=2048)

    # Parse JSON from the response
    raw = raw.strip()
    if raw.startswith("```"):
        # Strip markdown fences
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[recommender] Failed to parse LLM response as JSON: {raw[:200]}", flush=True)
        return []

    # Clear existing active LLM recommendations
    await db.execute(
        delete(Recommendation).where(
            Recommendation.user_id == user_id,
            Recommendation.source == "llm",
            Recommendation.status == "active",
        )
    )

    recommendations = []
    for item in items:
        rec = Recommendation(
            user_id=user_id,
            source="llm",
            category=item.get("category", "explore"),
            title=item.get("title", ""),
            summary=item.get("summary", ""),
            reasoning=item.get("reasoning", ""),
            concept_names=item.get("concept_names", []),
            priority=float(item.get("priority", 0.5)),
            status="active",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(rec)
        recommendations.append(rec)

    await db.flush()
    return recommendations


async def generate_web_recommendations(
    db: AsyncSession,
    user_id: uuid.UUID,
    browser_context: BrowserContext,
    llm_recommendations: Optional[list[Recommendation]] = None,
) -> list[Recommendation]:
    """Search the web for content relevant to the user's learning gaps."""
    # Extract search queries from LLM recommendations
    search_queries = []
    if llm_recommendations:
        for rec in llm_recommendations:
            if rec.category == "article":
                # Use concept_names to build a search query
                raw_names = rec.concept_names
                if isinstance(raw_names, list) and raw_names:
                    search_queries.append(" ".join(raw_names[:3]) + " tutorial explanation")
                elif rec.title:
                    search_queries.append(rec.title)

    # If no article recommendations, build queries from rusty/faded concepts
    if not search_queries and llm_recommendations:
        for rec in llm_recommendations:
            if rec.category == "review" and rec.concept_names:
                raw_names = rec.concept_names
                if isinstance(raw_names, list) and raw_names:
                    search_queries.append(raw_names[0] + " beginner guide")
                    if len(search_queries) >= 3:
                        break

    if not search_queries:
        return []

    # Clear existing active web recommendations
    await db.execute(
        delete(Recommendation).where(
            Recommendation.user_id == user_id,
            Recommendation.source == "web",
            Recommendation.status == "active",
        )
    )

    recommendations = []
    # Limit to 3 web searches to avoid being too slow
    for query in search_queries[:3]:
        search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        try:
            title, text, _ = await fetch_readable(search_url, browser_context)
        except WebFetchError as e:
            print(f"[recommender] Web fetch failed for '{query}': {e}", flush=True)
            continue

        # Ask LLM to evaluate the fetched content
        eval_prompt = (
            f"You found this web content while searching for '{query}':\n\n"
            f"Title: {title}\n"
            f"Content (first 2000 chars): {text[:2000]}\n\n"
            f"Is this content useful for a learner studying these topics? "
            f"Respond with JSON: {{\"useful\": true/false, \"title\": \"...\", "
            f"\"summary\": \"1-2 sentence summary\", \"priority\": 0.0-1.0}}"
        )
        eval_raw = await llm.generate(eval_prompt, max_tokens=256)
        eval_raw = eval_raw.strip()
        if eval_raw.startswith("```"):
            lines = eval_raw.split("\n")
            eval_raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            evaluation = json.loads(eval_raw)
        except json.JSONDecodeError:
            continue

        if not evaluation.get("useful", False):
            continue

        rec = Recommendation(
            user_id=user_id,
            source="web",
            category="article",
            title=evaluation.get("title", title),
            summary=evaluation.get("summary", ""),
            reasoning=f"Found via search for: {query}",
            url=search_url,
            concept_names=query.split()[:3],
            priority=float(evaluation.get("priority", 0.4)),
            status="active",
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        )
        db.add(rec)
        recommendations.append(rec)

    await db.flush()
    return recommendations
