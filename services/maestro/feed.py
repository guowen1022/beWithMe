"""Maestro feed engine — assemble (pure read) + select/dismiss + offline produce.

The feed is the browseable landing surface. The Maestro OWNS feed assembly:
it reads every persona's cached cards from the shared store, applies
per-persona saturation weights, and blends them into one ranked list.

Critical invariant (premise 5): **no LLM in the open path, and the open path
NEVER triggers generation.** `assemble()` only reads cached cards + sorts, so
opening the app is instant. Content is prepared *offline* — generation (slow
LLM) runs off-path in the persona producer, triggered by:

  - the session-end webhook (`post_event` on `user.engagement_ended`), and
  - the periodic scheduler tick (`scheduler_tick`, the "prepare again after
    ~a day" backstop).

Both call `schedule_produce()`, which is genuinely fire-and-forget (spawns an
`asyncio` task and returns immediately) and debounced so bursty events don't
thrash the producer. This mirrors how the inbox/kickoff pipeline is already
prepared from the same `engagement_ended` event.

Selecting a card seeds the Maestro cache under the card's `purpose`
(answer.py reads it) so the chosen persona's framing + posture ride into the
first turn — generalizing the old inbox tap → cache-seed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import httpx

from infra.contracts.event import StreamQuery
from infra.silicon_brain_client import SiliconBrainClient
from infra.topology import upstream_url

from services.maestro import blend as _blend
from services.maestro import saturation as _saturation
from services.maestro.cache import Cache, CacheEntry, VALID_POSTURES


# A feed is "stale" once its freshest active card is older than this (or none
# exist). The scheduler uses this as the "prepare again after ~a day" gate; the
# open path only reports it (for telemetry/UI) and never acts on it.
FEED_STALE_AFTER = timedelta(hours=24)

# Don't regenerate a feed more often than this on event-driven triggers. Session
# ends can arrive in bursts (idle close + re-engage); this debounce keeps the
# producer from re-running on every one while still refreshing a genuinely-old
# feed. The scheduler bypasses it (force=True) because it already gated on
# FEED_STALE_AFTER.
MIN_REGEN_INTERVAL = timedelta(hours=2)

# Personas whose producers the Maestro can trigger. Today: just the teacher.
# Each entry is the persona's produce endpoint on the persona sidecar.
_PRODUCER_ENDPOINTS = {
    "teacher": "/api/agent/produce-candidates",
}

# Hold strong refs to in-flight produce tasks so the event loop doesn't GC them
# mid-flight (asyncio keeps only weak refs). Same idiom as
# services/persona/routers/sessions.py.
_background_tasks: set[asyncio.Task] = set()


def _newest_created(cards: list) -> Optional[datetime]:
    newest = max(
        (c.created_at for c in cards if c.created_at is not None),
        default=None,
    )
    if newest is not None and newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return newest


def _is_stale(cards: list, now: datetime) -> bool:
    if not cards:
        return True
    newest = _newest_created(cards)
    if newest is None:
        return True
    return (now - newest) > FEED_STALE_AFTER


async def _has_resumable(client: SiliconBrainClient, user_id: UUID) -> bool:
    """True when the user has any prior engagement — the launcher uses this to
    decide whether to offer a 'Continue where you left off' starter card. Cheap:
    one indexed event read, no LLM."""
    try:
        rows = await client.query_stream(
            user_id,
            StreamQuery(
                kinds=["user.engagement_started", "user.engagement_ended"],
                limit=1,
                order="desc",
            ),
        )
        return bool(rows)
    except Exception:
        return False


async def assemble(
    client: SiliconBrainClient, user_id: UUID, *, now: Optional[datetime] = None,
) -> dict:
    """Read cached cards across personas → blend → return. PURE READ: no LLM and
    no generation trigger, so the app opens instantly. `stale` is returned for
    telemetry/UI only; preparation happens off the open path."""
    now = now or datetime.now(timezone.utc)
    try:
        cards = await client.list_feed_candidates(user_id, status="active", limit=50)
    except Exception as e:
        print(f"[maestro.feed] list failed: {e}", flush=True)
        cards = []

    personas = {c.source_persona for c in cards}
    weights = {p: await _saturation.persona_weight(client, user_id, p) for p in personas}
    ranked = _blend.rank(cards, weights)

    return {
        "cards": [
            {
                **rc.card.model_dump(mode="json"),
                "blended_score": rc.blended_score,
                "persona_weight": rc.persona_weight,
            }
            for rc in ranked
        ],
        "stale": _is_stale(cards, now),
        "has_resumable": await _has_resumable(client, user_id),
    }


def schedule_produce(user_id: UUID, *, force: bool = False) -> None:
    """Fire-and-forget feed (re)generation for `user_id`. Returns immediately;
    the debounce check + producer POST run in a background task so no caller
    (event webhook, scheduler tick, manual refresh) ever blocks on the LLM.

    `force=True` skips the MIN_REGEN_INTERVAL debounce — used by the explicit
    'Prepare new options' button and by the scheduler (which already gated on
    FEED_STALE_AFTER)."""
    task = asyncio.create_task(_produce(user_id, force=force))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _should_regen(
    client: SiliconBrainClient, user_id: UUID, *, now: Optional[datetime] = None,
) -> bool:
    """Regenerate only if the feed is empty or its freshest card is older than
    MIN_REGEN_INTERVAL — the debounce for bursty event-driven triggers."""
    now = now or datetime.now(timezone.utc)
    try:
        cards = await client.list_feed_candidates(user_id, status="active", limit=50)
    except Exception:
        return True
    if not cards:
        return True
    newest = _newest_created(cards)
    if newest is None:
        return True
    return (now - newest) > MIN_REGEN_INTERVAL


async def _produce(user_id: UUID, *, force: bool) -> None:
    """Debounce (unless forced), then POST every persona producer. Best-effort:
    failures are logged and swallowed — feed prep is additive substrate."""
    if not force:
        client = SiliconBrainClient()
        try:
            if not await _should_regen(client, user_id):
                return
        except Exception as e:
            print(f"[maestro.feed] regen check failed, producing anyway: {e}", flush=True)
        finally:
            await client.aclose()

    persona_url = upstream_url("persona")
    headers = {"X-User-Id": str(user_id)}
    for endpoint in _PRODUCER_ENDPOINTS.values():
        url = f"{persona_url}{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as h:
                await h.post(url, headers=headers)
        except Exception as e:
            print(f"[maestro.feed] produce failed ({url}): {e}", flush=True)


async def scheduler_tick(*, now: Optional[datetime] = None) -> int:
    """One scheduler pass: schedule a regenerate for every user whose feed is
    stale (older than FEED_STALE_AFTER) or empty. Returns how many users were
    scheduled. This is the offline 'prepare again after ~a day' backstop for
    users who keep the app open without ending a session."""
    now = now or datetime.now(timezone.utc)
    scheduled = 0
    client = SiliconBrainClient()
    try:
        user_ids = await client.list_feed_user_ids()
        for user_id in user_ids:
            try:
                cards = await client.list_feed_candidates(
                    user_id, status="active", limit=50,
                )
            except Exception as e:
                print(f"[maestro.feed] scheduler list failed for {user_id}: {e}", flush=True)
                continue
            if _is_stale(cards, now):
                schedule_produce(user_id, force=True)
                scheduled += 1
    except Exception as e:
        print(f"[maestro.feed] scheduler tick failed: {e}", flush=True)
    finally:
        await client.aclose()
    return scheduled


async def refresh(user_id: UUID) -> dict:
    """Explicit 'Prepare new options' — fire a background regenerate and return
    immediately. The client keeps the current cards visible and re-lists (on a
    short silent poll / on focus) to pick up the fresh batch when it lands."""
    schedule_produce(user_id, force=True)
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
