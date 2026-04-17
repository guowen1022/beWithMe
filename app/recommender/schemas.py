"""Pydantic schemas for recommendations."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class RecommendationRead(BaseModel):
    id: str
    source: str
    category: str
    title: str
    summary: str
    reasoning: str
    url: Optional[str] = None
    concept_names: list[str] = []
    priority: float
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RecommendationUpdate(BaseModel):
    status: str  # "dismissed" | "accepted"
