"""Feed candidate — one card a persona contributes to the unified feed.

The feed is the browseable landing surface (it replaces the old
Recommendations page and the Inbox tab). Every persona (teacher today;
comforter / helper later) produces its OWN ranked cards and writes them
here tagged with `source_persona`. The Maestro reads across all personas,
applies per-persona saturation, and blends them into one ranked feed —
the user browses and selects (never interrupted).

This generalizes the older `inbox_proposal` model: a card carries the
same `purpose` / `posture` / `opening` framing (so selecting one seeds
the Maestro cache exactly like an inbox tap did), plus a `source_persona`
and the persona's own `intra_rank` (its intra-source ranking, which the
Maestro never overrides — it only weights *between* personas).

Lifecycle:
  status='active'    — produced by a persona, eligible for the feed
  status='selected'  — the user picked this card to begin a session
  status='dismissed' — the user explicitly dismissed it
  status='expired'   — TTL passed, or pushed out by the per-persona cap

Per-user FK CASCADE so infra/user_data.py auto-discovers it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow_tz() -> datetime:
    return datetime.now(timezone.utc)


class FeedCandidate(Base):
    __tablename__ = "feed_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # Which persona authored this card: "teacher", "comforter", "helper", …
    # The Maestro blends ACROSS these; saturation is computed per-persona.
    source_persona: Mapped[str] = mapped_column(Text, index=True)
    # Persona-purpose tag (e.g. teacher:long-horizon-propose). Keys the
    # Maestro cache when the card is selected — answer.py reads this key.
    purpose: Mapped[str] = mapped_column(Text)
    # Posture this card establishes if selected (steady, deepen, …).
    posture: Mapped[str] = mapped_column(Text, default="steady")
    title: Mapped[str] = mapped_column(Text)
    # Short paragraph that becomes the engagement's first-turn frame.
    opening: Mapped[str] = mapped_column(Text)
    # The persona's OWN ranking of this card within its set, 0..1. The
    # Maestro multiplies this by a per-persona saturation weight to blend.
    intra_rank: Mapped[float] = mapped_column(Float, default=0.5)
    # Optional persona-specific kind tag (e.g. teacher's review|explore|deepen).
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Free-form extras (concept_names, url, …). JSONB.
    body: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow_tz, index=True,
    )
    selected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_feed_user_status_created", "user_id", "status", "created_at"),
        Index("ix_feed_user_persona_status", "user_id", "source_persona", "status"),
    )
