"""Inbox proposal — one tappable card shown to the learner.

Each row is ONE candidate from a Maestro kickoff (SPEC §6.1). When a
kickoff decision produces K candidates, the agent layer writes K
inbox_proposal rows; they cluster in the UI under a shared
`kickoff_event_id`. The user taps one (or dismisses all); the tapped
proposal seeds the engagement's cache with that candidate's
opening + posture.

Lifecycle:
  status='pending'  — written by agent, awaiting user
  status='tapped'   — user tapped; consumed by engagement seeding
  status='dismissed'— user explicitly closed without picking
  status='expired'  — TTL passed (PR-7's logic)
  status='consumed' — the engagement helper has seeded the cache from
                      this row; the row stays for audit but won't
                      re-fire on the next turn.

Per-user FK CASCADE so infra/user_data.py auto-discovers it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow_tz() -> datetime:
    return datetime.now(timezone.utc)


class InboxProposal(Base):
    __tablename__ = "inbox_proposals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # Group key — proposals from the same kickoff share this. The agent
    # uses it in the UI to render the K candidates under one header.
    kickoff_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True,
    )
    # Which candidate (0..K-1) from the kickoff this proposal realizes.
    candidate_idx: Mapped[int] = mapped_column(
        # Integer column; SQLAlchemy stores Python int directly.
        # Default to 0 so single-candidate kickoffs don't need to set it.
        default=0,
    )
    title: Mapped[str] = mapped_column(Text)
    # Persona-purpose tag (e.g., teacher:long-horizon-propose). The
    # engagement helper uses this to key the cache when seeding.
    persona_purpose: Mapped[str] = mapped_column(Text)
    # Posture this proposal would establish if tapped (steady, deepen, etc).
    posture: Mapped[str] = mapped_column(Text, default="steady")
    # Short paragraph the agent will turn into the engagement's first turn.
    opening: Mapped[str] = mapped_column(Text)
    # Free-form extras (e.g., concept_names, source_link). JSONB.
    body: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, default="pending", index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow_tz, index=True,
    )
    tapped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_inbox_user_status_created", "user_id", "status", "created_at"),
        Index("ix_inbox_user_kickoff", "user_id", "kickoff_event_id"),
    )
