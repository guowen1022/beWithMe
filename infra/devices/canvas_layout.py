"""CanvasLayout — which block lives on which device's canvas.

Infra-owned device/canvas topology — the runtime decision "this block is
mounted on the laptop right now", keyed by `(user_id, device_id, block_id)`
so a restart preserves per-device canvas state. It sits in `infra/devices/`
next to its siblings — `models.py` (the `devices` table it FKs to via
`devices.device_id`), `registry.py` (live presence), and `delivery.py` (the
live SSE channel + mount tracker). User-keyed, but device/canvas topology, not
silicon_brain episodic memory. Both foreign keys (`users.id`, `devices.device_id`)
are metadata string references, so this module imports nothing upward.

Block source code lives in the user's per-user-git workspace
(`data/canvases/<user_id>/blocks/<id>.js`), portable across devices. This table
records only the runtime mount decision, not the source.

Composite PK keeps a single block from being assigned twice to the same device.
A block can appear on multiple devices (different rows, different device_id).
"""
import uuid
from datetime import datetime

from sqlalchemy import Text, DateTime, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


class CanvasLayout(Base):
    __tablename__ = "canvas_layout"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "device_id", "block_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.device_id", ondelete="CASCADE"),
    )
    block_id: Mapped[str] = mapped_column(Text)
    mounted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
