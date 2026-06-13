"""Wire DTOs for the unified feed-candidate surface.

Mirrors silicon_brain.models.FeedCandidate columns; lives in infra so
neither side imports the other's ORM. Status is kept as an open string
to match the existing contracts pattern.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(from_attributes=True, extra="ignore")


class FeedCandidateDTO(BaseModel):
    model_config = _CFG

    id: UUID
    user_id: UUID
    source_persona: str
    purpose: str
    posture: str
    title: str
    opening: str
    intra_rank: float = 0.5
    category: Optional[str] = None
    body: Optional[dict[str, Any]] = None
    status: str
    created_at: Optional[datetime] = None
    selected_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class FeedCandidateCreate(BaseModel):
    """One card to write. `user_id` comes from the header."""
    model_config = _CFG

    source_persona: str = Field(..., min_length=1, max_length=64)
    purpose: str = Field(..., min_length=1, max_length=128)
    posture: str = "steady"
    title: str = Field(..., min_length=1, max_length=200)
    opening: str = Field(..., min_length=1)
    intra_rank: float = 0.5
    category: Optional[str] = None
    body: Optional[dict[str, Any]] = None
    expires_at: Optional[datetime] = None


class FeedCandidateReplace(BaseModel):
    """Replace one persona's active cards with a fresh batch (atomic).

    `source_persona` scopes the delete; every item must carry the same
    `source_persona`. This is how a persona's producer publishes a new
    generation without leaving stale cards behind.
    """
    model_config = _CFG

    source_persona: str = Field(..., min_length=1, max_length=64)
    items: List[FeedCandidateCreate] = Field(default_factory=list)
