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

from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(extra="ignore")


class GridPos(BaseModel):
    model_config = _CFG
    x: int = Field(ge=0, le=159)
    y: int = Field(ge=0, le=89)
    w: int = Field(ge=1, le=160)
    h: int = Field(ge=1, le=90)


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


__all__ = [
    "GridPos",
    "BlockSpec",
    "BlockSource",
    "UIUpdate",
    "BlockMessage",
    "BlockError",
]
