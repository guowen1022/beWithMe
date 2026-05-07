"""DTOs for the perception subsystem.

Producers (frontend blocks, tools/speak.py) push BlockState / VoiceUtterance
into the cache. The persona's read_media tool returns MediaPerception, which
extends infra/contracts/devices.MediaInventory with per-block state and
per-voice recent utterances.

Same pydantic posture as infra/contracts/ui.py and devices.py:
`extra="ignore"` so adding fields stays additive.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from infra.contracts.devices import DeviceClass
from infra.contracts.ui import GridPos


_CFG = ConfigDict(from_attributes=True, extra="ignore")


FocusState = Literal["active", "visible", "background"]


class BlockState(BaseModel):
    """One block's self-reported state. Latest-wins; no history kept.

    Posted to /api/dynamic/state/{block_id}; returned by read_media().

    `completed` is the trigger signal for event-driven persona turns.
    Set to True when an interactive block has finished a discrete unit of
    work (an upload completed, a form submitted, a wizard step done).
    The cache edge-detects false→true transitions and fires a
    BlockCompletedEvent so the trigger orchestrator can wake the teacher.
    Never True for a block that's continuously reporting state (a PDF
    being scrolled is "in progress" indefinitely).
    """
    model_config = _CFG
    kind: str = "snapshot"
    content: str = ""
    focus: FocusState = "visible"
    completed: bool = False
    # Optional — frontend reports the block's effective grid (what the user
    # actually sees) so the teacher's prompt can show layout coordinates and
    # decide whether to call layout_blocks. None until the block has had a
    # chance to self-report at least once.
    grid: Optional[GridPos] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class VoiceUtterance(BaseModel):
    """One spoken utterance — what the persona itself said, when, where."""
    model_config = _CFG
    text: str
    voice: Optional[str] = None
    device_id: Optional[UUID] = None
    played_at: datetime


class UserUtterance(BaseModel):
    """One spoken utterance from the user, captured by the ambient_mic block.

    Ephemeral. Lives only in the perception cache deque; never written to
    silicon_brain. `target_persona` is the routing key — each persona's
    trigger orchestrator filters on this.
    """
    model_config = _CFG
    text: str
    language: Optional[str] = None
    audio_duration_s: Optional[float] = None
    device_id: Optional[UUID] = None
    captured_at: datetime
    target_persona: str = "teacher"


# ---------- change-listener event payloads (P6 hook) ----------


class BlockChangeEvent(BaseModel):
    """Fired by cache.record_block_state when a block's state changes."""
    model_config = _CFG
    type: Literal["block-change"] = "block-change"
    user_id: UUID
    device_id: UUID
    block_id: str
    state: BlockState


class BlockCompletedEvent(BaseModel):
    """Edge-detected: fired exactly once when a block transitions
    completed=False → completed=True. The trigger orchestrator listens
    for this to wake the teacher's tool loop with no user message.
    """
    model_config = _CFG
    type: Literal["block-completed"] = "block-completed"
    user_id: UUID
    device_id: UUID
    block_id: str
    state: BlockState


class VoiceEvent(BaseModel):
    """Fired by cache.record_voice on every utterance."""
    model_config = _CFG
    type: Literal["voice"] = "voice"
    user_id: UUID
    utterance: VoiceUtterance


class UserSpeechEvent(BaseModel):
    """Fired by cache.record_user_speech when the user speaks.

    Distinct from VoiceEvent (which is the persona's own speech).
    `target_persona` is duplicated on the event so listeners can filter
    cheaply without inspecting the utterance payload.
    """
    model_config = _CFG
    type: Literal["user-speech"] = "user-speech"
    user_id: UUID
    utterance: UserUtterance
    target_persona: str


PerceptionEvent = Union[BlockChangeEvent, BlockCompletedEvent, VoiceEvent, UserSpeechEvent]


# ---------- read_media() return shape ----------


class BlockSummary(BaseModel):
    """One block as it appears on a canvas (id + title + latest state)."""
    model_config = _CFG
    id: str
    title: Optional[str] = None
    state: Optional[BlockState] = None
    last_updated_s_ago: Optional[float] = None


class CanvasPerception(BaseModel):
    """One device's screen surface, with per-block state."""
    model_config = _CFG
    device_id: UUID
    device_class: DeviceClass
    online: bool
    blocks: List[BlockSummary] = []


class VoicePerception(BaseModel):
    """One device's audio output, with the persona's recent utterances on it."""
    model_config = _CFG
    device_id: UUID
    device_class: DeviceClass
    online: bool
    recent_utterances: List[VoiceUtterance] = []


class MediaPerception(BaseModel):
    """The persona's full perception view — answer to read_media()."""
    model_config = _CFG
    user_id: UUID
    canvases: List[CanvasPerception] = []
    voices: List[VoicePerception] = []


__all__ = [
    "FocusState",
    "BlockState",
    "VoiceUtterance",
    "UserUtterance",
    "BlockChangeEvent",
    "BlockCompletedEvent",
    "VoiceEvent",
    "UserSpeechEvent",
    "PerceptionEvent",
    "BlockSummary",
    "CanvasPerception",
    "VoicePerception",
    "MediaPerception",
]
