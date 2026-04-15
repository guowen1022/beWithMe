"""ORM models for the knowledge graph: concept nodes and edges."""
import uuid
from typing import Optional
from datetime import datetime
from sqlalchemy import Text, DateTime, Integer, Float, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db_base import Base


class ConceptNode(Base):
    __tablename__ = "concept_nodes"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="new")
    encounter_count: Mapped[int] = mapped_column(Integer, default=1)
    half_life_hours: Mapped[float] = mapped_column(Float, default=24.0)
    last_recalled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ConceptEdge(Base):
    __tablename__ = "concept_edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("concept_nodes.id", ondelete="CASCADE"))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("concept_nodes.id", ondelete="CASCADE"))
    edge_type: Mapped[str] = mapped_column(Text, default="temporal")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_reinforced: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
