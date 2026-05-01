"""Wire contracts shared between persona and silicon_brain.

These DTOs are the typed shape passed over HTTP between the persona sidecar
(consumer) and the knowledge sidecar (provider). They mirror silicon_brain
ORM rows but live in `infra` so neither side imports the other's model
classes.

Both sides set:
  - `from_attributes = True` so silicon_brain can do `DTO.model_validate(orm_row)`
  - `extra = "ignore"` so adding a new column to silicon_brain doesn't
    break callers that haven't adopted it yet.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict


_CFG = ConfigDict(from_attributes=True, extra="ignore")


# --- Read DTOs ---


class ProfileDTO(BaseModel):
    model_config = _CFG
    user_id: UUID
    self_description: str = ""
    created_at: Optional[datetime] = None


class UserProfileDTO(BaseModel):
    model_config = _CFG
    explanation_style: str = "balanced"
    depth_preference: str = "moderate"
    analogy_affinity: str = "moderate"
    math_comfort: str = "moderate"
    pacing: str = "moderate"
    meta_notes: str = ""
    preference_embedding: Optional[List[float]] = None
    session_interest_summary: Optional[str] = None


class ConceptDTO(BaseModel):
    model_config = _CFG
    id: UUID
    name: str
    state: str  # solid | learning | rusty | faded
    mastery_p: Optional[float] = None
    half_life_hours: Optional[float] = None
    encounter_count: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_recalled_at: Optional[datetime] = None


class InteractionDTO(BaseModel):
    model_config = _CFG
    id: UUID
    user_id: UUID
    session_id: Optional[UUID] = None
    parent_interaction_id: Optional[UUID] = None
    title: Optional[str] = None
    passage_text: Optional[str] = None
    question: str = ""
    answer: str = ""
    source_document: Optional[str] = None
    created_at: Optional[datetime] = None


class DocumentChunkDTO(BaseModel):
    model_config = _CFG
    id: UUID
    document_id: UUID
    chunk_index: int
    text: str


class SummaryDTO(BaseModel):
    model_config = _CFG
    session_id: UUID
    file_path: str
    similarity: Optional[float] = None
    content: str = ""


class BrainStateDTO(BaseModel):
    model_config = _CFG
    self_description: str = ""
    profile: Optional[UserProfileDTO] = None
    concept_nodes: List[ConceptDTO] = []
    graph_context: str = ""


class RecommendationDTO(BaseModel):
    model_config = _CFG
    id: UUID
    user_id: UUID
    source: str  # "llm" | "web"
    category: str
    title: str = ""
    summary: str = ""
    reasoning: str = ""
    concept_names: List[str] = []
    priority: float = 0.5
    status: str = "active"
    expires_at: Optional[datetime] = None
    url: Optional[str] = None
    created_at: Optional[datetime] = None


# --- Write DTOs ---


class RecommendationCreateDTO(BaseModel):
    """Input shape for POST /api/recommendations/replace-active."""
    model_config = _CFG
    source: str = "llm"
    category: str
    title: str = ""
    summary: str = ""
    reasoning: str = ""
    concept_names: List[str] = []
    priority: float = 0.5
    expires_at: Optional[datetime] = None
    url: Optional[str] = None


class InteractionCreateDTO(BaseModel):
    """Input shape for POST /api/interactions."""
    model_config = _CFG
    session_id: Optional[UUID] = None
    parent_interaction_id: Optional[UUID] = None
    title: Optional[str] = None
    passage_text: Optional[str] = None
    question: str
    answer: str = ""
    source_document: Optional[str] = None


class SessionSummaryUpsertDTO(BaseModel):
    """Input shape for POST /api/sessions/summaries."""
    model_config = _CFG
    session_id: UUID
    file_path: str
    labels: List[str] = []
    embedding: Optional[List[float]] = None


from infra.contracts.ui import (  # noqa: E402
    GridPos,
    BlockSpec,
    BlockSource,
    UIUpdate,
    BlockMessage,
    BlockError,
)


__all__ = [
    "ProfileDTO",
    "UserProfileDTO",
    "ConceptDTO",
    "InteractionDTO",
    "DocumentChunkDTO",
    "SummaryDTO",
    "BrainStateDTO",
    "RecommendationDTO",
    "RecommendationCreateDTO",
    "InteractionCreateDTO",
    "SessionSummaryUpsertDTO",
    # ui contracts
    "GridPos",
    "BlockSpec",
    "BlockSource",
    "UIUpdate",
    "BlockMessage",
    "BlockError",
]
