"""Silicon brain schemas — data shapes for user profile, interactions, documents."""

from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


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
