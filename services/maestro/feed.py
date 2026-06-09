"""Maestro feed engine — assemble (fast) + select/dismiss + produce trigger.

The feed is the browseable landing surface. The Maestro OWNS feed assembly:
it reads every persona's cached cards from the shared store, applies
per-persona saturation weights, and blends them into one ranked list.

Critical invariant (premise 5): **no LLM in the open path.** `assemble()` only
reads cached cards + sorts. Generation (slow LLM) happens off-path in the
persona producer; `assemble()` merely *triggers* it (fire-and-forget) when the
feed is empty or stale, and returns whatever is cached now.

Selecting a card seeds the Maestro cache under the card's `purpose`
(answer.py reads it) so the chosen persona's framing + posture ride into the
first turn — generalizing the old inbox tap → cache-seed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import httpx

from infra.silicon_brain_client import SiliconBrainClient
from infra.topology import upstream_url

from services.maestro import blend as _blend
from services.maestro import saturation as _saturation
from services.maestro.cache import Cache, CacheEntry, VALID_POSTURES


# Regenerate when the freshest active card is older than this (or none exist).
FEED_STALE_AFTER = timedelta(hours=6)

# Personas whose producers the Maestro can trigger. Today: just the teacher.
# Each entry is the persona's produce endpoint on the persona sidecar.
_PRODUCER_ENDPOINTS = {
    "teacher": "/api/agent/produce-candidates",
}


def _is_stale(cards: list, now: datetime) -> bool:
    if not cards:
        return True
    newest = max(
        (c.created_at for c in cards if c.created_at is not None),
        default=None,
    )
    if newest is None:
        return True
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (now - newest) > FEED_STALE_AFTER


async def _notify_produce(user_id: UUID, *, wait: bool) -> None:
    """Ask every known persona producer to (re)generate its cards.

    `wait=False` (assemble's auto-trigger) is fire-and-forget — failures are
    logged and swallowed so the fast open path never depends on producer
    health. `wait=True` (explicit refresh) awaits so the caller can re-list
    fresh cards immediately."""
    persona_url = upstream_url("persona")
    headers = {"X-User-Id": str(user_id)}
    for endpoint in _PRODUCER_ENDPOINTS.values():
        url = f"{persona_url}{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as h:
                resp = await h.post(url, headers=headers)
                if wait:
                    resp.raise_for_status()
        except Exception as e:
            print(f"[maestro.feed] produce notify failed ({url}): {e}", flush=True)
            if wait:
                raise


async def assemble(
    client: SiliconBrainClient, user_id: UUID, *, now: Optional[datetime] = None,
) -> dict:
    """Read cached cards across personas → blend → return. Triggers async
    (re)generation when the feed is empty/stale. FAST: no LLM here."""
    now = now or datetime.now(timezone.utc)
    try:
        cards = await client.list_feed_candidates(user_id, status="active", limit=50)
    except Exception as e:
        print(f"[maestro.feed] list failed: {e}", flush=True)
        cards = []

    personas = {c.source_persona for c in cards}
    weights = {p: await _saturation.persona_weight(client, user_id, p) for p in personas}
    ranked = _blend.rank(cards, weights)

    stale = _is_stale(cards, now)
    if stale:
        # Fire-and-forget: refresh for next open; return what we have now.
        await _notify_produce(user_id, wait=False)

    return {
        "cards": [
            {
                **rc.card.model_dump(mode="json"),
                "blended_score": rc.blended_score,
                "persona_weight": rc.persona_weight,
            }
            for rc in ranked
        ],
        "stale": stale,
    }


async def refresh(user_id: UUID) -> dict:
    """Explicit regenerate ("Prepare new options"). Awaits production so the
    caller can re-list fresh cards."""
    await _notify_produce(user_id, wait=True)
    return {"ok": True}


async def select(
    client: SiliconBrainClient, cache: Cache, user_id: UUID, card_id: UUID,
) -> dict:
    """Mark a card selected (store emits user.card_selected) and seed the
    Maestro cache with its framing so the first turn lands in the right
    posture."""
    card = await client.select_feed_candidate(user_id, card_id)
    posture = card.posture if card.posture in VALID_POSTURES else "steady"
    try:
        await cache.set(CacheEntry(
            user_id=user_id,
            persona_purpose=card.purpose,
            paragraph=card.opening,
            posture=posture,
        ))
    except Exception as e:
        print(f"[maestro.feed] cache seed failed: {e}", flush=True)
    return card.model_dump(mode="json")


async def dismiss(
    client: SiliconBrainClient, user_id: UUID, card_id: UUID,
) -> dict:
    """Mark a card dismissed (store emits user.card_dismissed)."""
    card = await client.dismiss_feed_candidate(user_id, card_id)
    return card.model_dump(mode="json")
