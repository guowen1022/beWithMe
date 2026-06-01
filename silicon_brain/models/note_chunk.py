import uuid
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.config import settings
from infra.db import Base


class NoteChunk(Base):
    """Chunk of a markdown note authored by the teacher.

    A note lives on disk as one .md file per (user_id, note_id) — see
    `workshop/canvas/tools/_note_cache.py`. The chunker groups consecutive
    markdown blocks (target ~250 words) and stores each group as one row
    with `(block_start, block_end)` so a search hit can scroll to the
    exact block span in the rendered note.

    Embeddings are written with nomic's `search_document:` task prefix and
    queried with `search_query:` — both are applied server-side in the
    knowledge sidecar's notes router, never in clients.
    """

    __tablename__ = "note_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    note_id: Mapped[str] = mapped_column(Text)
    block_start: Mapped[int] = mapped_column(Integer)
    block_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(settings.embedding_dim), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index("ix_note_chunks_user_note", "user_id", "note_id"),
    )
