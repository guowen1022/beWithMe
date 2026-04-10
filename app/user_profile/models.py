"""ORM model for user learning preferences."""

import uuid
from typing import Optional
from datetime import datetime
from sqlalchemy import Text, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column
from app.db_base import Base


class LearningPreferences(Base):
    __tablename__ = "learning_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    explanation_style: Mapped[str] = mapped_column(Text, default="balanced")
    depth_preference: Mapped[str] = mapped_column(Text, default="moderate")
    analogy_affinity: Mapped[str] = mapped_column(Text, default="moderate")
    math_comfort: Mapped[str] = mapped_column(Text, default="moderate")
    pacing: Mapped[str] = mapped_column(Text, default="moderate")
    meta_notes: Mapped[str] = mapped_column(Text, default="")
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    last_distilled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    preference_embedding = mapped_column(Vector(768), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
