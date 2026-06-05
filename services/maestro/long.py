"""Maestro long instance — the proactive planner (SPEC §6).

Inputs: events the long instance subscribes to (engagement_ended,
capture.*, agent.followup_scheduled becoming due, periodic ticks).
Output: a `maestro_long.kickoff_decision` event, either:
  - SILENCE — the most common case; body carries the rationale.
  - ACT — body carries 1..K candidates plus the gate's rationale and
    propensity. PR-5 wakes the agent on these.

This module is the "long instance" of the Maestro machinery; the short
instance (PR-6) shares infra (cache, LLM facade) but subscribes to
in-engagement signals and writes the cache substrate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import httpx

from infra.contracts.event import EventDTO, EventEmit, StreamQuery
from infra.silicon_brain_client import SiliconBrainClient
from infra.topology import upstream_url

from services.maestro import candidates as _candidates
from services.maestro import gate as _gate
from services.maestro import slice as _slice


# Phase-0 default K — the heuristic gate doesn't have any sophistication
# yet for tuning K per-event, so we ask for the upper bound and let the
# LLM trim itself when it can't honestly produce diverse candidates.
DEFAULT_K = 3


async def _build_gate_input(
    client: SiliconBrainClient,
    user_id: UUID,
    triggering: EventDTO,
    now: datetime,
) -> _gate.GateInput:
    """Assemble the heuristic gate's inputs from event-stream reads."""
    # Latest engagement_ended for this user.
    last_engagement_ended: Optional[datetime] = None
    try:
        rows = await client.query_stream(
            user_id,
            StreamQuery(
                kinds=["user.engagement_ended"], limit=1, order="desc",
            ),
        )
        if rows:
            last_engagement_ended = rows[0].ts
    except Exception:
        pass

    # Followups whose valid_at is now-or-past.
    due_followups_count = 0
    try:
        rows = await client.query_stream(
            user_id,
            StreamQuery(
                kinds=["agent.followup_scheduled"], limit=50, order="desc",
            ),
        )
        due_followups_count = sum(
            1 for r in rows if r.valid_at is not None and r.valid_at <= now
        )
    except Exception:
        pass

    # PR-7 surfaces inbox stock; until then assume 0 — the cap rule then
    # collapses to a no-op (won't suppress ACT prematurely).
    open_inbox_count = 0

    return _gate.GateInput(
        user_id=user_id,
        triggering_kind=triggering.kind,
        now=now,
        last_engagement_ended=last_engagement_ended,
        open_inbox_count=open_inbox_count,
        due_followups_count=due_followups_count,
    )


async def handle_event(
    triggering: EventDTO,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Drive one full pass: gate → slice → candidates (if ACT) → emit.

    Returns the decision body the kickoff_decision event was given —
    useful for tests + the webhook response. Errors propagate to the
    caller; webhook handlers catch + return 500 / log.
    """
    now = now or datetime.now(timezone.utc)
    user_id = triggering.user_id

    client = SiliconBrainClient()
    try:
        gate_input = await _build_gate_input(client, user_id, triggering, now)
        decision = _gate.decide(gate_input)

        body: dict = {
            "decision": decision.decision,
            "rationale": decision.rationale,
            "propensity": decision.propensity,
            "triggering_event_id": str(triggering.event_id),
            "triggering_kind": triggering.kind,
        }

        if decision.decision == "ACT":
            substrate = await _slice.retrieve(
                client, user_id, triggering, now=now,
            )
            slice_text = _slice.render_for_prompt(substrate)
            cands = await _candidates.generate(
                slice_text, k=DEFAULT_K, user_id=user_id,
            )
            body["candidates"] = [c.to_dict() for c in cands]
            body["k"] = len(cands)
            if not cands:
                # LLM declined to produce candidates → effectively SILENCE.
                # Log it that way so the eventual training pipeline sees
                # the gate-said-ACT-but-no-output case distinctly.
                body["decision"] = "SILENCE"
                body["rationale"] = (
                    body["rationale"]
                    + " — candidate generation returned 0 (substrate too thin)"
                )
        else:
            body["candidates"] = []
            body["k"] = 0

        kickoff_event = await client.emit_event(
            user_id,
            EventEmit(
                kind="maestro_long.kickoff_decision",
                source="maestro_long",
                body=body,
                refs={"triggering_event_id": str(triggering.event_id)},
            ),
        )

        # On ACT-with-candidates, fire the kickoff-realization webhook so
        # the persona side writes K inbox proposals. Best-effort — the
        # event already landed in the stream, so a webhook failure just
        # means the proposals aren't materialized; the audit trail is fine.
        if body["decision"] == "ACT" and body.get("candidates"):
            await _notify_agent_kickoff(
                user_id=user_id,
                kickoff_event_id=kickoff_event.event_id,
                candidates=body["candidates"],
            )

        return body
    finally:
        await client.aclose()


async def _notify_agent_kickoff(
    *, user_id: UUID, kickoff_event_id: UUID, candidates: list[dict],
) -> None:
    """POST /api/agent/kickoff on the persona sidecar."""
    try:
        url = f"{upstream_url('persona')}/api/agent/kickoff"
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as h:
            resp = await h.post(
                url,
                headers={"X-User-Id": str(user_id)},
                json={
                    "kickoff_event_id": str(kickoff_event_id),
                    "user_id": str(user_id),
                    "candidates": candidates,
                },
            )
            resp.raise_for_status()
    except Exception as e:
        print(f"[maestro.long] kickoff webhook failed: {e}", flush=True)
