"""DTOs for the dynamic UI subsystem.

These types are shared between three places that don't import each other:
  * agents/frontend_engineer/ — produces a BlockSource.
  * tools/request_ui_block.py — emits a UIUpdate to the dynamic stream.
  * services/persona/routers/dynamic.py — multiplexes UIUpdate / BlockMessage /
    BlockError over SSE to the frontend.

Mirrors the PoC (block-canvas/lib/types.ts) shape so the browser-side eval
loop and these contracts agree on what a block looks like.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(extra="ignore")


# Per-device-class grid bounds. Mirrors frontend/lib/gridConfig.ts so the
# server validates against the same dimensions the frontend renders. 12 cols
# on desktop is the Bootstrap convention — strong LLM prior for col-6/4/3
# layouts; 4→8→12 cascade scales cleanly to tablet and phone. Rows stay at
# 9 so vertical reasoning is uniform across breakpoints.
DeviceClass = Literal["phone", "tablet", "desktop"]

DEVICE_GRID_BOUNDS: Dict[str, Tuple[int, int]] = {
    "phone":   (4,  9),
    "tablet":  (8,  9),
    "desktop": (12, 9),
}


def grid_bounds_for(device_class: Optional[str]) -> Tuple[int, int]:
    """Return `(cols, rows)` for the given device class.

    Falls back to desktop when the class is unknown or None — desktop is
    the largest grid and the safest default for ambiguous cases.
    """
    if device_class is None:
        return DEVICE_GRID_BOUNDS["desktop"]
    return DEVICE_GRID_BOUNDS.get(device_class, DEVICE_GRID_BOUNDS["desktop"])


class GridPos(BaseModel):
    """A block's position on the device-class grid.

    Bounds are intentionally permissive at the schema level (just `ge=0`/
    `ge=1`); device-aware upper bounds are enforced by tool call sites
    that know the target device, via `grid_bounds_for(device_class)`.
    """
    model_config = _CFG
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1)
    h: int = Field(ge=1)


class BlockSpec(BaseModel):
    """What a tool/persona asks the engineer to build.

    Free-form on purpose — v1 just carries the user's command. The engineer
    reads the user's existing workspace (per-user git of blocks) and decides
    what to add/modify/remove based on the description.
    """
    model_config = _CFG
    description: str = ""
    suggested_id: Optional[str] = None
    user_id: Optional[UUID] = None


class BlockSource(BaseModel):
    """The artifact the engineer produces.

    `source` is a parens-wrapped JS object expression that evaluates to a
    `{ id, grid, content, style, run, ... }` block (PoC convention). The
    browser does the eval — see frontend/lib/dynamic.ts.
    """
    model_config = _CFG
    id: str
    source: str
    design_doc: Optional[str] = None


class UIUpdate(BaseModel):
    """SSE event: 'a block was added/replaced/removed on the canvas'."""
    model_config = _CFG
    type: Literal["ui-update"] = "ui-update"
    action: Literal["mount", "replace", "unmount"] = "mount"
    block: BlockSource


class BlockMessage(BaseModel):
    """SSE event: 'push this value into block X's bus topic Y'.

    The teacher (or any backend code) calls POST /api/dynamic/push/{block_id}
    and the dynamic router fans this out to the user's SSE channel. The
    frontend translates it into bus.publish(topic, value).
    """
    model_config = _CFG
    type: Literal["block-data"] = "block-data"
    block_id: str
    topic: str
    value: Any = None


class BlockError(BaseModel):
    """SSE event: 'browser-side eval failed for block X'.

    Reported by the frontend via POST /api/dynamic/error/{block_id}, then
    fanned back over SSE so future engineer/agent retries can see it.
    """
    model_config = _CFG
    type: Literal["block-error"] = "block-error"
    block_id: str
    error: str


class BlockAction(BaseModel):
    """SSE event: 'invoke a standard handle on block X'.

    Drives the per-block handles introduced in commit 739e931 (scroll/
    highlight/focus). The frontend looks the block up in its registry
    and calls the corresponding method.
    """
    model_config = _CFG
    type: Literal["block-action"] = "block-action"
    block_id: str
    action: Literal["highlight", "focus", "scroll_to", "raise", "set_grid"]
    options: Dict[str, Any] = Field(default_factory=dict)


class TeacherThinking(BaseModel):
    """SSE event: 'the teacher (or any persona) just ran an LLM call' —
    for the dev "llm thinking" panel, not the chat. Carries a summary of
    what fired, the tool calls made, and any text emitted (capped).
    The frontend treats it as transient debug info.

    `trigger` carries the scenario name ("answer", "reflect", "router",
    "recommender", "distiller", "goal-planner", "session-summarizer",
    "delegate-engineer", "block-completed", "canvas-changed", "voice").
    The `model` / `provider` / `*_tokens` / `latency_ms` fields are
    populated by the LLM facade wrap and are absent for events emitted
    outside an LLM call.
    """
    model_config = _CFG
    type: Literal["teacher-thinking"] = "teacher-thinking"
    phase: Literal["start", "end"]
    trigger: str                            # scenario or trigger name
    summary: str = ""                       # one-line description
    text: Optional[str] = None              # teacher's text output (end)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    # Populated by the LLM facade observability wrap:
    model: Optional[str] = None             # e.g. "deepseek-v4-pro"
    provider: Optional[str] = None          # e.g. "deepseek"
    prompt_tokens: Optional[int] = None     # input + cache_read
    completion_tokens: Optional[int] = None
    latency_ms: Optional[int] = None        # total wall-clock


class VoicePlay(BaseModel):
    """SSE event: 'speak this text on the user's chosen device'.

    The frontend's SpeakerSink fetches `/api/speak/stream` itself with
    these params and pipes the PCM into Web Audio. We carry the text
    rather than a synthesized blob so each connected device can play at
    its own pace and the SSE channel stays text-only.
    """
    model_config = _CFG
    type: Literal["voice-play"] = "voice-play"
    text: str
    voice: Optional[str] = None
    speed: Optional[float] = None
    lang: Optional[str] = None


__all__ = [
    "DeviceClass",
    "DEVICE_GRID_BOUNDS",
    "grid_bounds_for",
    "GridPos",
    "BlockSpec",
    "BlockSource",
    "UIUpdate",
    "BlockMessage",
    "BlockError",
    "BlockAction",
    "TeacherThinking",
    "VoicePlay",
]
