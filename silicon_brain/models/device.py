"""Device — a connected client (browser tab, phone, desktop app) for a user.

Registered when the frontend opens its SSE channel. The frontend issues a
stable `device_id` in localStorage and replays it on every reconnect, so the
row's `first_seen` is durable across restarts and `last_seen` reflects the
most recent connect.

Capabilities are a freeform JSON blob (`{display, speaker, mic}`) so we can
add new ones without a migration.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    device_class: Mapped[str] = mapped_column(Text, default="desktop")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
