"""Teacher agent schemas — request/response shapes for the teaching API."""

from typing import Literal, Optional, List
from uuid import UUID, uuid4
from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    passage_text: Optional[str] = None
    selected_text: Optional[str] = None
    question: str
    document_id: Optional[UUID] = None
    session_id: UUID = Field(default_factory=uuid4)
    parent_interaction_id: Optional[UUID] = None
    prompt_version: Literal["v1", "v2"] = "v2"


class SignalRequest(BaseModel):
    """Lightweight signal from block-level interaction (e.g. 'got it', 'review later')."""
    session_id: UUID
    parent_interaction_id: UUID
    block_text: str
    signal: Literal["got_it", "review_later"]


class AskResponse(BaseModel):
    interaction_id: UUID
    answer: str
    session_id: UUID
    title: Optional[str] = None
    related_interaction_ids: List[UUID] = []
