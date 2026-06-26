"""Wire contract for the persona sidecar's ask/dispatch entry point.

`AskRequest` is the shared request body for `POST /api/ask*` — the single entry
the shell proxies to the persona sidecar. Its `addressee` field is the dispatch
key that routes the turn to one persona (teacher / app_operator /
frontend_engineer).

It lives in `infra/contracts` rather than inside any one persona's package so
that adding a new persona does not force an edit to another persona's files
(architecture-review F10). Each persona consumes this DTO; none owns it.
"""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    passage_text: Optional[str] = None
    selected_text: Optional[str] = None
    question: str
    document_id: Optional[UUID] = None
    session_id: UUID = Field(default_factory=uuid4)
    parent_interaction_id: Optional[UUID] = None
    # Routing addressee. Default 'teacher' — message goes through the
    # teacher's intent router. 'frontend_engineer' bypasses the router and
    # forwards the message straight to the engineer (for E2E debugging
    # without the LLM router round-trip). 'app_operator' routes to the
    # app_operator persona, whose "app actions" tools change the app shell
    # (switch user, go home, show mirror) rather than answer a question.
    addressee: Literal["teacher", "frontend_engineer", "app_operator"] = "teacher"
