"""ORM model for recommendations."""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Text, DateTime, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db_base import Base


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(Text)  # "llm" | "web"
    category: Mapped[str] = mapped_column(Text)  # "review" | "explore" | "deepen" | "article"
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    concept_names: Mapped[dict] = mapped_column(JSONB, default=list)
    priority: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(Text, default="active")  # "active" | "dismissed" | "accepted"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_recommendations_user_status", "user_id", "status"),
    )
