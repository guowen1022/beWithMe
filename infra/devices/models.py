"""Device ORM — a connected client (browser tab, phone, desktop app).

Infra-level device-registry state for the media → visual → canvas system:
which screens / speakers / mics a user has reachable, so output can be routed
and the teacher's media inventory knows what's on screen. Keyed by `user_id`
(a device belongs to whoever is signed in on it), but this is *infra-owned*
device topology — not silicon_brain episodic memory. It lives next to its
siblings: `infra/devices/registry.py` (live presence + durable mirror) and
`infra/contracts/devices.py` (the wire DTO). The `users.id` foreign key is a
metadata-level string reference, so this module imports nothing from
silicon_brain — infra stays the leaf.

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
