"""Wire DTOs for the inbox proposal surface (PR-5).

Mirrors silicon_brain.models.InboxProposal columns; lives in infra so
neither side imports the other's ORM. Status is kept as an open string
to match the existing contracts pattern (so a future PR adding a new
status like 'snoozed' doesn't break the contract).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(from_attributes=True, extra="ignore")


class InboxProposalDTO(BaseModel):
    model_config = _CFG

    id: UUID
    user_id: UUID
    kickoff_event_id: UUID
    candidate_idx: int
    title: str
    persona_purpose: str
    posture: str
    opening: str
    body: Optional[dict[str, Any]] = None
    status: str
    created_at: Optional[datetime] = None
    tapped_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None


class InboxProposalCreate(BaseModel):
    """POST /api/inbox input. user_id from header."""
    model_config = _CFG

    kickoff_event_id: UUID
    candidate_idx: int = 0
    title: str = Field(..., min_length=1, max_length=200)
    persona_purpose: str = Field(..., min_length=1, max_length=128)
    posture: str = "steady"
    opening: str = Field(..., min_length=1)
    body: Optional[dict[str, Any]] = None
