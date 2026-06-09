"""Teacher feed producer — publish the teacher's cards into the shared store.

Reuses the recommender's reasoning (`reason_candidate_items`) and maps each
ranked item onto a `FeedCandidate`:

  - source_persona = "teacher"
  - purpose        = "teacher:long-horizon-propose"  (the Maestro-cache key
                     answer.py already reads, so selecting a card frames the
                     first turn)
  - posture        = category → posture map
  - opening        = the item's `summary` (the first-turn framing paragraph)
  - intra_rank     = the item's `priority` (the teacher's own ranking)

This runs OFF the feed-open path (slow LLM, cached): the Maestro fires the
persona's produce endpoint when the feed is stale; the open path only reads.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from infra.contracts.feed import FeedCandidateCreate, FeedCandidateDTO
from infra.silicon_brain_client import SiliconBrainClient
from persona.teacher.recommender.engine import reason_candidate_items


SOURCE_PERSONA = "teacher"
PURPOSE = "teacher:long-horizon-propose"

# Teacher categories → SPEC §5.7 postures. A fresh start is "steady" unless
# the learner is going one level deeper on something they partly know.
_CATEGORY_TO_POSTURE = {
    "review": "steady",
    "explore": "steady",
    "deepen": "deepen",
}


def _clamp01(x: float) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.5


async def produce_teacher_feed(
    db: AsyncSession, user_id: UUID, client: SiliconBrainClient,
) -> list[FeedCandidateDTO]:
    """Reason → map → replace the teacher's active cards in the shared store."""
    self_description = ""
    try:
        profile = await client.get_profile(user_id)
        self_description = profile.self_description if profile else ""
    except Exception as e:
        print(f"[feed.producer] could not fetch profile: {e}", flush=True)

    items = await reason_candidate_items(db, user_id, self_description)

    creates: list[FeedCandidateCreate] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        summary = str(it.get("summary") or "").strip()
        if not title or not summary:
            continue
        category = str(it.get("category") or "explore").strip().lower()
        creates.append(FeedCandidateCreate(
            source_persona=SOURCE_PERSONA,
            purpose=PURPOSE,
            posture=_CATEGORY_TO_POSTURE.get(category, "steady"),
            title=title[:200],
            opening=summary,
            intra_rank=_clamp01(it.get("priority", 0.5)),
            category=category,
            body={
                "concept_names": it.get("concept_names", []),
                "reasoning": str(it.get("reasoning") or ""),
            },
        ))

    # Always replace (even with an empty batch) so a stale generation is
    # cleared and the feed reflects the latest reasoning.
    return await client.replace_feed_candidates(user_id, SOURCE_PERSONA, creates)
