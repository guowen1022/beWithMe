"""silicon_brain schemas — neutral user-data shapes (Profile, Document)."""

from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


class ProfileRead(BaseModel):
    self_description: str
    created_at: datetime


class ProfileUpdate(BaseModel):
    self_description: str


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
