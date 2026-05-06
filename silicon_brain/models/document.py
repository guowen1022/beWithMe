import uuid
from typing import Any, Optional, List
from datetime import datetime
from sqlalchemy import Text, DateTime, Integer, ForeignKey, LargeBinary
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship
from infra.db import Base
from infra.config import settings


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    pdf_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    # Flat table-of-contents extracted from PDF bookmarks at upload, shape
    # [{"title": str, "page": int}, ...]. None when the PDF has no
    # bookmarks (most non-academic PDFs).
    outline: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    # Persisted page count so /structure can answer without re-parsing
    # pdf_data. Nullable for back-compat with rows from before this column.
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    chunks: Mapped[List["DocumentChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # 1-indexed page the chunk belongs to. Null for legacy chunks written
    # before per-page chunking.
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    embedding = mapped_column(Vector(settings.embedding_dim), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    document: Mapped["Document"] = relationship(back_populates="chunks")
