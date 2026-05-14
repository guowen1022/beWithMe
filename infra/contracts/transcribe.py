"""DTOs for the transcribe sidecar's auxiliary endpoints.

The main `/api/transcribe` route stays untyped at the HTTP boundary
(multipart form + simple dict response) — these contracts are for the
text-only auxiliary endpoints layered onto the same sidecar.

Today: `/api/eou` (LiveKit text turn-detector). Tomorrow: whatever
small perception primitive next lives next to whisper.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(extra="ignore")


class EouTurn(BaseModel):
    """One side of a recent exchange, fed to the EOU model as context."""
    model_config = _CFG
    role: Literal["user", "assistant"]
    text: str


class EouRequest(BaseModel):
    """Score the probability that the user is done speaking.

    `transcripts` is the *current* user turn, chunked into the phrases
    silero-VAD has produced so far (oldest first). They are concatenated
    with single spaces before scoring — the model sees a single rolling
    user turn, not a list.

    `prior_turns` is optional conversation history (oldest first). Most
    callers pass [] — the model is reasonable on a single user turn.
    """
    model_config = _CFG
    transcripts: List[str] = Field(default_factory=list)
    prior_turns: List[EouTurn] = Field(default_factory=list)
    # Optional override; falls back to settings.eou_threshold.
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class EouResponse(BaseModel):
    model_config = _CFG
    end_prob: float = Field(ge=0.0, le=1.0)
    end_of_turn: bool
    threshold: float
    infer_ms: float


__all__ = ["EouTurn", "EouRequest", "EouResponse"]
