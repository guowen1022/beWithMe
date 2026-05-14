"""Event ingestion endpoint — frontend/client side actions land here.

Browser code POSTs `{kind, fields}` to /api/events and we relay the payload
to the project event log. This makes user-side actions (typed-vs-voice
submit, button clicks, page navigations) end up in the same JSONL file as
sidecar HTTP and pipeline-stage events, so a single tail covers the whole
session.

Auth: the shell gateway already gates everything with X-User-Id. We don't
re-verify here; we just stamp the user_id from the header onto the event
so consumers can filter.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from infra.event_log import log_event


router = APIRouter()


class IngestEvent(BaseModel):
    kind: str = Field(..., min_length=1, max_length=128)
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/events")
async def ingest_event(
    body: IngestEvent,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    if not body.kind:
        raise HTTPException(status_code=400, detail="kind required")
    # `source="client"` so consumers can tell at a glance which side of the
    # wire a row came from. Sidecars stamp `service=<name>` automatically.
    log_event(
        body.kind,
        source="client",
        user_id=x_user_id,
        **body.fields,
    )
    return {"ok": True}
