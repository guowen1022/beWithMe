"""UserPreferences — what the user says they prefer (set via PUT /api/preferences).

User-stated, persona-agnostic. Each persona may interpret these differently:
the teacher reads `analogy_affinity=high` and may pick analogies eagerly;
a different persona might use the same field for a different decision.

The teacher's *interpretation* — preference embedding, distillation counter
— lives in `persona/teacher/models/teacher_preference_model.py`, not here.
"""
import uuid
from datetime import datetime
from sqlalchemy import Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Categorical preferences set by the user.
    explanation_style: Mapped[str] = mapped_column(Text, default="balanced")
    depth_preference: Mapped[str] = mapped_column(Text, default="moderate")
    analogy_affinity: Mapped[str] = mapped_column(Text, default="moderate")
    math_comfort: Mapped[str] = mapped_column(Text, default="moderate")
    pacing: Mapped[str] = mapped_column(Text, default="moderate")
    meta_notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
