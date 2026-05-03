"""DTOs for the device + media inventory subsystem.

Shared between:
  * infra/devices/registry.py — owns the live registry, returns Device DTOs.
  * tools/list_media.py — assembles MediaInventory for the teacher.
  * services/persona/routers/dynamic.py — registers devices on SSE connect.

Same `extra="ignore"` posture as ui.py so adding fields stays additive.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(from_attributes=True, extra="ignore")

DeviceClass = Literal["phone", "tablet", "desktop"]


class DeviceCapabilities(BaseModel):
    model_config = _CFG
    display: bool = True
    speaker: bool = False
    mic: bool = False


class Device(BaseModel):
    """A connected (or last-seen) client for one user."""
    model_config = _CFG
    device_id: UUID
    user_id: UUID
    device_class: DeviceClass = "desktop"
    capabilities: DeviceCapabilities = Field(default_factory=DeviceCapabilities)
    online: bool = False
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class CanvasBlockSummary(BaseModel):
    """A single block as it appears on a canvas (one row of list_media)."""
    model_config = _CFG
    id: str
    title: Optional[str] = None  # first-line of design_doc, if available


class Canvas(BaseModel):
    """One device's screen surface and its currently-mounted blocks."""
    model_config = _CFG
    device_id: UUID
    device_class: DeviceClass
    online: bool
    blocks: List[CanvasBlockSummary] = []


class Voice(BaseModel):
    """One device's audio output. Voice prefs land here in P3."""
    model_config = _CFG
    device_id: UUID
    device_class: DeviceClass
    online: bool


class MediaInventory(BaseModel):
    """The teacher's media surface — the answer to `list_media()`."""
    model_config = _CFG
    user_id: UUID
    canvases: List[Canvas] = []
    voices: List[Voice] = []


__all__ = [
    "DeviceClass",
    "DeviceCapabilities",
    "Device",
    "CanvasBlockSummary",
    "Canvas",
    "Voice",
    "MediaInventory",
]
