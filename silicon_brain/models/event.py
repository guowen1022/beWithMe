"""Per-learner event stream — the substrate of the Maestro architecture.

A single append-only stream per user. Every learner-facing decision and
every observation by the Maestro / agent / signal sources lands here in a
uniform envelope (SPEC §8.2). Domain stores (`profile`, `user_preferences`,
…) remain authoritative for current state; this is the additive
observational history those stores are derived from.

Distinct from `infra/event_log.py`: that is a server-side JSONL
observability log (HTTP request lines, client-side button clicks). This is
the persisted, per-user, durable substrate the Maestro reasons over.

Per SPEC §8.3 `kind` is an open versioned enum:
    user.*           — learner actions (engagement_started, proposal_tapped, ...)
    system.*         — system-attributed transitions
    maestro_long.*   — long-instance decisions (kickoff_decision, …)
    maestro_short.*  — short-instance refreshes (cache_refresh, posture_change, …)
    agent.*          — persona observations / followups
    signal.*         — in-engagement signals (turn_arrived, flow_marker, …)
    capture.*        — reality-capture lifecycle

# TODO partitioning: SPEC §8 calls for monthly time-partitioning. `init_db.py`
# uses SQLAlchemy `create_all` which has no native partition support. The
# composite indexes below carry per-user recency + kind-filtered queries
# until volume warrants a switch to Alembic + explicit DDL. Tracked in
# TODO.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow_tz() -> datetime:
    """tz-AWARE UTC. Used instead of `datetime.utcnow` (naive) because
    PostgreSQL's TIMESTAMPTZ binds naive datetimes against the SESSION
    timezone — on a host whose locale is e.g. Asia/Shanghai (UTC+8) the
    naive value is interpreted as local-wall-clock and stored 8 hours
    off. The legacy models in this package mostly avoid the trap because
    they never compute deltas between stored `ts` and Python wall time;
    `events` is the first table that genuinely depends on tz-correctness.
    """
    return datetime.now(timezone.utc)


class Event(Base):
    """One row of the per-user append-only event stream."""

    __tablename__ = "events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Per-user FK with CASCADE so a user wipe (infra/user_data.py auto-discovers
    # this table via the `user_id` column) takes the stream with it.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # When the system recorded the event. Server-stamped, monotonic per user.
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow_tz, index=True
    )
    # When the event is semantically true (e.g. a `agent.followup_scheduled`
    # for tomorrow). Defaults to `ts` server-side when omitted.
    valid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(Text, index=True)
    kind: Mapped[str] = mapped_column(Text, index=True)
    body: Mapped[Any] = mapped_column(JSONB, nullable=False)
    refs: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        Index("ix_events_user_ts", "user_id", "ts"),
        Index("ix_events_user_kind_ts", "user_id", "kind", "ts"),
        Index("ix_events_user_valid_at", "user_id", "valid_at"),
    )
