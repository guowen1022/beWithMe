from typing import Optional, List
from uuid import UUID, uuid4
from datetime import datetime
from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    passage_text: Optional[str] = None
    selected_text: Optional[str] = None
    question: str
    document_id: Optional[UUID] = None
    session_id: UUID = Field(default_factory=uuid4)
    parent_interaction_id: Optional[UUID] = None


class AskResponse(BaseModel):
    interaction_id: UUID
    answer: str
    session_id: UUID
    title: Optional[str] = None
    related_interaction_ids: List[UUID] = []


class ProfileRead(BaseModel):
    self_description: str
    created_at: datetime


class ProfileUpdate(BaseModel):
    self_description: str


class InteractionRead(BaseModel):
    id: UUID
    session_id: UUID
    passage_text: Optional[str]
    question: str
    answer: str
    source_document: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentCreate(BaseModel):
    title: str
    content: str
    filename: Optional[str] = None


class DocumentRead(BaseModel):
    id: UUID
    title: str
    filename: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
