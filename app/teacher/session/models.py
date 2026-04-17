import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column
from app.db_base import Base
from app.config import settings


class SessionSummary(Base):
    __tablename__ = "session_summaries"
    __table_args__ = (
        UniqueConstraint("user_id", "session_id", name="uq_session_summaries_user_session"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(settings.embedding_dim), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
