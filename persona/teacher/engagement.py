"""Engagement-boundary state machine for the teacher persona.

An "engagement" is a continuous block of user attention. Bounded by:

  - 5-min inactivity (lazy: detected and back-dated on the NEXT turn)
  - Explicit close (TODO — not wired in PR-3)
  - System-detected completion (TODO — Maestro decision in PR-6+)
  - `wind_down` posture followed by close (TODO — PR-5/PR-6)

Re-engagement window: a turn arriving within RE_WINDOW after an
`engagement_ended` event reopens the SAME `engagement_id`. Outside that
window, a new `engagement_id` is minted.

Storage: no separate table. State is derived from the event stream on
each turn — the latest of `user.engagement_started` /
`user.engagement_ended` / `signal.turn_arrived` determines the current
posture. This matches the SPEC §8 "event stream is the substrate"
principle and keeps PR-3 free of new schema.

Call site: every persona entry point that constitutes a user turn must
call `ensure_engagement_and_emit_turn(user_id, source)` BEFORE invoking
the LLM tool loop. Today that's:

  - services/persona/routers/ask.py  (`source="ask"`)
  - persona/teacher/triggers.py:_execute_conversation (`source="voice"`)

Idempotency: calling twice in rapid succession (e.g. two near-
simultaneous turns) will emit two `signal.turn_arrived` events but only
one `engagement_started` per genuine engagement boundary. The lazy
idle-detection means clock-skew between calls cannot corrupt state.

Layer note: per ARCHITECTURE.md, persona reaches silicon_brain over
HTTP, not by direct DB import. The helper instantiates a fresh
SiliconBrainClient per call — Phase-0 simplicity; can pool in Phase 1+
if per-turn overhead becomes a concern.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import httpx

from infra.contracts.event import EventDTO, EventEmit, StreamQuery
from infra.silicon_brain_client import SiliconBrainClient
from infra.topology import upstream_url


# Phase-0 defaults — both 5min per SPEC. Kept as module constants so
# tests can monkeypatch tighter values without touching production code.
IDLE_THRESHOLD = timedelta(minutes=5)
RE_WINDOW = timedelta(minutes=5)


_ACTIVITY_KINDS = (
    "user.engagement_started",
    "user.engagement_ended",
    "signal.turn_arrived",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _latest(client: SiliconBrainClient, user_id: UUID, kinds: list[str]) -> Optional[EventDTO]:
    rows = await client.query_stream(
        user_id,
        StreamQuery(kinds=kinds, limit=1, order="desc"),
    )
    return rows[0] if rows else None


async def _emit(
    client: SiliconBrainClient,
    user_id: UUID,
    kind: str,
    body: dict,
    valid_at: Optional[datetime] = None,
) -> EventDTO:
    return await client.emit_event(
        user_id,
        EventEmit(kind=kind, source="user", body=body, valid_at=valid_at),
    )


async def _notify_maestro(triggering: EventDTO) -> None:
    """Fire the Maestro long-instance webhook. Best-effort: any failure
    (Maestro sidecar down, slow, etc.) is logged and swallowed — the
    user-facing turn must not depend on Maestro health."""
    try:
        url = f"{upstream_url('maestro')}/api/maestro/event"
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as h:
            resp = await h.post(url, json={"event": triggering.model_dump(mode="json")})
            resp.raise_for_status()
    except Exception as e:
        print(f"[engagement] maestro notify failed: {e}", flush=True)


async def ensure_engagement_and_emit_turn(
    user_id: UUID,
    source: str,
    *,
    now: Optional[datetime] = None,
) -> UUID:
    """Ensure there's a live engagement for `user_id` and emit
    `signal.turn_arrived` for the current turn.

    Returns the active `engagement_id`. May emit (in order):
      - `user.engagement_ended` (back-dated to `last_activity + IDLE_THRESHOLD`)
        if the prior engagement was idle past the threshold.
      - `user.engagement_started` if no prior engagement exists, the
        prior was ended outside RE_WINDOW, or we just closed an idle one.
      - `signal.turn_arrived` always.

    `source` is recorded in the `signal.turn_arrived` body so consumers
    can tell text vs voice turns apart without joining other tables.
    """
    now = now or _now()
    client = SiliconBrainClient()
    try:
        latest = await _latest(client, user_id, list(_ACTIVITY_KINDS))

        # Decide which engagement_id the current turn belongs to, and emit
        # any boundary events the state machine demands.
        if latest is None:
            engagement_id = uuid4()
            await _emit(client, user_id, "user.engagement_started",
                        {"engagement_id": str(engagement_id)})

        elif latest.kind == "user.engagement_ended":
            elapsed = now - latest.ts
            prior_id = latest.body.get("engagement_id")
            if elapsed <= RE_WINDOW and prior_id:
                # Re-engagement window — reopen the same id.
                engagement_id = UUID(prior_id)
            else:
                engagement_id = uuid4()
            await _emit(client, user_id, "user.engagement_started",
                        {"engagement_id": str(engagement_id)})

        else:
            # Either engagement_started or signal.turn_arrived — engagement
            # is currently "open" from the stream's point of view. Check
            # whether it actually went idle since the latest activity.
            elapsed = now - latest.ts

            # Resolve the current engagement_id. If latest is
            # engagement_started, body has it. Otherwise (signal.turn_arrived),
            # find the most recent engagement_started.
            if latest.kind == "user.engagement_started":
                current_id = latest.body.get("engagement_id")
            else:
                started = await _latest(client, user_id, ["user.engagement_started"])
                current_id = started.body.get("engagement_id") if started else None
            current_id = UUID(current_id) if current_id else uuid4()

            if elapsed > IDLE_THRESHOLD:
                # Implicitly end at last_activity + IDLE_THRESHOLD.
                ended_at = latest.ts + IDLE_THRESHOLD
                ended_event = await _emit(
                    client, user_id, "user.engagement_ended",
                    {"engagement_id": str(current_id)},
                    valid_at=ended_at,
                )
                # Notify the Maestro long instance that an engagement
                # closed. Decision (ACT vs SILENCE) lives in the Maestro;
                # this side just rings the bell.
                await _notify_maestro(ended_event)
                # Decide reopen-vs-new based on RE_WINDOW from the
                # implicit end (not from `now`, so back-to-back idle +
                # reactivation produces predictable boundaries).
                if (now - ended_at) <= RE_WINDOW:
                    engagement_id = current_id
                else:
                    engagement_id = uuid4()
                await _emit(client, user_id, "user.engagement_started",
                            {"engagement_id": str(engagement_id)})
            else:
                # Still active.
                engagement_id = current_id

        # Always emit signal.turn_arrived.
        await client.emit_event(
            user_id,
            EventEmit(
                kind="signal.turn_arrived",
                source="signal",
                body={"engagement_id": str(engagement_id), "turn_source": source},
            ),
        )

        return engagement_id
    finally:
        await client.aclose()


__all__ = [
    "ensure_engagement_and_emit_turn",
    "IDLE_THRESHOLD",
    "RE_WINDOW",
]
