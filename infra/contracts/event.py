"""Wire DTOs for the per-user event stream (SPEC §8).

Persona ↔ silicon_brain HTTP contract. `Event` lives in silicon_brain;
neither side imports the other's ORM classes — they meet on these DTOs.

`source` and `kind` are kept as `str` (open enum per SPEC §8.3). New kinds
are added at the call site without changing the contract.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(from_attributes=True, extra="ignore")


# --- Read DTOs ---


class EventDTO(BaseModel):
    """Full event row returned by emit / query."""
    model_config = _CFG

    event_id: UUID
    user_id: UUID
    ts: datetime
    valid_at: Optional[datetime] = None
    source: str
    kind: str
    body: dict[str, Any] = Field(default_factory=dict)
    refs: Optional[dict[str, Any]] = None
    schema_version: int = 1


# --- Write DTOs ---


class EventEmit(BaseModel):
    """Input shape for POST /api/event-stream.

    `user_id` is supplied via the `X-User-Id` header — not in the body —
    to match the rest of the silicon_brain HTTP surface. `ts` is
    server-stamped. `valid_at` defaults to `ts` server-side when omitted.
    """
    model_config = _CFG

    kind: str = Field(..., min_length=1, max_length=128)
    source: str = Field(..., min_length=1, max_length=64)
    body: dict[str, Any] = Field(default_factory=dict)
    refs: Optional[dict[str, Any]] = None
    valid_at: Optional[datetime] = None
    schema_version: int = 1


class StreamQuery(BaseModel):
    """Input shape for POST /api/event-stream/query.

    All filters are AND-ed. `since`/`until` are inclusive/exclusive of `ts`.
    Result order is by `ts` (default newest first).
    """
    model_config = _CFG

    kinds: Optional[list[str]] = None
    sources: Optional[list[str]] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=1000)
    order: Literal["asc", "desc"] = "desc"
