"""TeacherPreferenceModel — teacher's distilled interpretation of a user's preferences.

The user's explicit prefs ("I like gaming") live in `UserPreferences`
(silicon_brain). This table is teacher's *interpretation* of the user — built
from the EMA loop over interaction embeddings plus periodic LLM distillation.

The categorical fields here MIRROR the user's UserPreferences shape on
purpose: `distill_preferences()` writes inferred values here, the user
*can't* edit it, and consumers can compare "user-said" vs "teacher-thinks"
to spot drift, conflict, or surprise.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Text, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


class TeacherPreferenceModel(Base):
    __tablename__ = "teacher_preference_model"
    __table_args__ = (UniqueConstraint("user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Teacher's distilled categorical view (mirrors UserPreferences shape).
    explanation_style: Mapped[str] = mapped_column(Text, default="balanced")
    depth_preference: Mapped[str] = mapped_column(Text, default="moderate")
    analogy_affinity: Mapped[str] = mapped_column(Text, default="moderate")
    math_comfort: Mapped[str] = mapped_column(Text, default="moderate")
    pacing: Mapped[str] = mapped_column(Text, default="moderate")
    meta_notes: Mapped[str] = mapped_column(Text, default="")

    # EMA-blended dense fingerprint of the user's interaction style.
    preference_embedding = mapped_column(Vector(768), nullable=True)

    # Distillation trigger: every N new interactions, re-run the LLM distill.
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    last_distilled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
