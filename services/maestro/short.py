"""Maestro short instance — signal-driven cache refresh (SPEC §5, §9).

Subscribes to in-engagement signals (signal.turn_arrived,
signal.flow_marker, signal.environment_shift, signal.distress_marker,
signal.tool_call_result). Updates the cache entry for the active
engagement's persona-purpose, honoring posture monotonicity.

Phase-0 design: NO LLM call in the refresh. The substrate slice + the
existing paragraph + the signal kind together determine whether the
posture should move and whether to emit a refresh event. Paragraph
prose is not rewritten yet — Phase 1+ adds a small-model paragraph
refresh once the wire is exercised under load.

Skip-refresh policy (SPEC §5.4):
  - cache age < MIN_REFRESH_INTERVAL → skip (too frequent)
  - signal.kind == signal.turn_arrived AND no flow marker → skip in
    Phase 0 (no per-turn classifier yet)
  - otherwise → refresh

Every decision (refresh or skip) writes a `maestro_short.cache_refresh`
or `maestro_short.skip_refresh` event so the cache_refresh_log view
can audit the short instance's behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from infra.contracts.event import EventDTO, EventEmit
from infra.silicon_brain_client import SiliconBrainClient

from services.maestro import posture as _posture
from services.maestro.cache import Cache, CacheEntry


# Minimum interval between two refreshes of the same cache key. Cheap
# guard against signal floods (e.g. a user typing 5 turns in 30s).
MIN_REFRESH_INTERVAL = timedelta(seconds=30)

# Active-engagement TTL. After this many seconds the cache is stale
# enough that the next read should force a refresh regardless.
ACTIVE_TTL = timedelta(minutes=5)


# Which signal kinds the short instance considers "strong enough to
# refresh on" in Phase 0. The first three carry semantic content; the
# others are mostly heartbeat and dominated by the TTL fallback.
_STRONG_SIGNALS = frozenset({
    "signal.flow_marker",
    "signal.environment_shift",
    "signal.distress_marker",
})


# Per-signal posture hint. The short instance uses this to PROPOSE a
# posture change; posture.permit_transition then applies monotonicity.
# `signal.turn_arrived` carries no semantic posture suggestion in
# Phase 0 — the existing posture rides through.
_SIGNAL_POSTURE_HINT: dict[str, str] = {
    "signal.distress_marker": "interrupt_now",
    "signal.environment_shift": "hold",
    # signal.flow_marker would carry a posture in its body (Phase 1+).
}


# The single persona-purpose Phase-0 PR-5/PR-6 keys against. PR-7+ will
# diversify the cache to multiple purposes (in-engagement vs
# long-horizon vs etc.).
_ACTIVE_PURPOSE = "teacher:long-horizon-propose"


async def _emit(
    client: SiliconBrainClient,
    user_id: UUID,
    kind: str,
    body: dict,
) -> EventDTO:
    return await client.emit_event(
        user_id,
        EventEmit(kind=kind, source="maestro_short", body=body),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def handle_signal(
    cache: Cache,
    triggering: EventDTO,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Process one in-engagement signal. Returns the decision body
    for the test/webhook surface."""
    now = now or _now()
    user_id = triggering.user_id

    client = SiliconBrainClient()
    try:
        entry = await cache.get(user_id, _ACTIVE_PURPOSE)
        signal_kind = triggering.kind

        # No cache entry yet → nothing to refresh; record the skip.
        if entry is None:
            body = {
                "decision": "skip_refresh",
                "rationale": "no cache entry to refresh against yet",
                "triggering_event_id": str(triggering.event_id),
                "signal_kind": signal_kind,
            }
            await _emit(client, user_id, "maestro_short.skip_refresh", body)
            return body

        # Throttle: too soon since last refresh.
        age = now - entry.written_at
        if age < MIN_REFRESH_INTERVAL:
            body = {
                "decision": "skip_refresh",
                "rationale": (
                    f"cache age {age.total_seconds():.1f}s < "
                    f"MIN_REFRESH_INTERVAL {MIN_REFRESH_INTERVAL.total_seconds():.0f}s"
                ),
                "triggering_event_id": str(triggering.event_id),
                "signal_kind": signal_kind,
                "prior_posture": entry.posture,
            }
            await _emit(client, user_id, "maestro_short.skip_refresh", body)
            return body

        # Phase-0 reasoning gate: only "strong" signals OR TTL force a
        # refresh. signal.turn_arrived gets skipped unless the TTL fired.
        ttl_fired = age >= ACTIVE_TTL
        is_strong = signal_kind in _STRONG_SIGNALS
        if not is_strong and not ttl_fired:
            body = {
                "decision": "skip_refresh",
                "rationale": (
                    f"signal {signal_kind!r} not in strong set; "
                    f"TTL not yet fired (age {age.total_seconds():.1f}s)"
                ),
                "triggering_event_id": str(triggering.event_id),
                "signal_kind": signal_kind,
                "prior_posture": entry.posture,
            }
            await _emit(client, user_id, "maestro_short.skip_refresh", body)
            return body

        # Refresh path. Posture: propose from signal hint (or keep
        # existing if no hint); apply monotonicity. Body hint
        # `user_initiated` overrides monotonic block when set.
        body_hint = triggering.body or {}
        proposed_posture = (
            _SIGNAL_POSTURE_HINT.get(signal_kind)
            or body_hint.get("posture")
            or entry.posture
        )
        user_initiated = bool(body_hint.get("user_initiated", False))
        final_posture, transition_note = _posture.permit_transition(
            entry.posture, proposed_posture, user_initiated=user_initiated,
        )

        # Phase-0 short instance keeps the paragraph; only the timestamp
        # (and maybe posture) moves. Phase 1+ swaps in a real LLM refresh.
        new_entry = CacheEntry(
            user_id=entry.user_id,
            persona_purpose=entry.persona_purpose,
            paragraph=entry.paragraph,
            posture=final_posture,
            written_at=now,
            candidate_idx=entry.candidate_idx,
        )
        await cache.set(new_entry)

        body = {
            "decision": "cache_refresh",
            "rationale": (
                f"strong signal {signal_kind!r}" if is_strong
                else f"TTL fired after {age.total_seconds():.0f}s"
            ),
            "triggering_event_id": str(triggering.event_id),
            "signal_kind": signal_kind,
            "prior_posture": entry.posture,
            "new_posture": final_posture,
            "posture_transition": transition_note,
        }
        await _emit(client, user_id, "maestro_short.cache_refresh", body)
        return body
    finally:
        await client.aclose()
