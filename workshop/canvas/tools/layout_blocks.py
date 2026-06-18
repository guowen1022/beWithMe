"""layout_blocks — batch grid reflow for the dynamic canvas.

The teacher uses this to resize/reposition multiple blocks at once on the
device-class grid (12×9 desktop, 8×9 tablet, 4×9 phone — see
`infra/contracts/ui.DEVICE_GRID_BOUNDS`). Internally fans out one
BlockAction(set_grid) SSE per layout entry. The frontend's setGrid handler
mutates inline gridColumn / gridRow only — block source is NOT
re-evaluated, so PDFs don't reload and bus subscriptions don't drop.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from infra.contracts.ui import BlockAction, GridPos, grid_bounds_for
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from infra.model.tools import ToolSpec, ToolDomain


async def layout_blocks(
    *,
    user_id: UUID,
    layouts: List[Dict[str, Any]],
    target_device_id: Optional[UUID] = None,
    device_class: Optional[str] = None,
) -> Dict[str, Any]:
    """Reflow a list of blocks. Returns {moved: [...], total_delivered: N}.

    Each entry must be a dict with `block_id` (str) and `x, y, w, h` (int).
    Out-of-bounds entries raise ValueError before any SSE is fanned out — we
    don't want partial reflows where some blocks moved and others were
    silently rejected.

    `device_class` ("phone" | "tablet" | "desktop") drives the bounds. When
    omitted, validation runs against the desktop grid (12×9), which is the
    largest and most permissive — safe when the persona doesn't yet know
    which device it's targeting.
    """
    if not isinstance(layouts, list) or not layouts:
        raise ValueError("layouts must be a non-empty list")

    cols, rows = grid_bounds_for(device_class)

    validated: list[tuple[str, GridPos]] = []
    for i, entry in enumerate(layouts):
        if not isinstance(entry, dict):
            raise ValueError(f"layouts[{i}] must be an object")
        block_id = (entry.get("block_id") or "").strip()
        if not block_id:
            raise ValueError(f"layouts[{i}].block_id is required")
        try:
            grid = GridPos(
                x=int(entry["x"]),
                y=int(entry["y"]),
                w=int(entry["w"]),
                h=int(entry["h"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"layouts[{i}] invalid x/y/w/h: {e}")
        if grid.x >= cols:
            raise ValueError(f"layouts[{i}] x must be < {cols} on {device_class or 'desktop'}")
        if grid.y >= rows:
            raise ValueError(f"layouts[{i}] y must be < {rows} on {device_class or 'desktop'}")
        if grid.x + grid.w > cols:
            raise ValueError(f"layouts[{i}] x+w must be <= {cols} on {device_class or 'desktop'}")
        if grid.y + grid.h > rows:
            raise ValueError(f"layouts[{i}] y+h must be <= {rows} on {device_class or 'desktop'}")
        validated.append((block_id, grid))

    moved: list[str] = []
    total_delivered = 0
    for block_id, grid in validated:
        event = BlockAction(
            block_id=block_id,
            action="set_grid",
            options={"x": grid.x, "y": grid.y, "w": grid.w, "h": grid.h},
        )
        if target_device_id is not None:
            n = await enqueue_for_device(user_id, target_device_id, event)
        else:
            n = await enqueue_for_user(user_id, event)
        moved.append(block_id)
        total_delivered += n

    return {"moved": moved, "total_delivered": total_delivered}


__all__ = ["layout_blocks", "build_spec"]

def _make_layout_blocks(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        layouts = args.get("layouts")
        if not isinstance(layouts, list) or not layouts:
            return json.dumps({"error": "layouts must be a non-empty list"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        device_class = args.get("device_class")
        if device_class is not None and device_class not in ("phone", "tablet", "desktop"):
            return json.dumps({"error": "device_class must be 'phone', 'tablet', or 'desktop'"})
        try:
            result = await layout_blocks(
                user_id=user_id,
                layouts=layouts,
                target_device_id=target_uuid,
                device_class=device_class,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="layout_blocks",
        description=(
            "Resize and reposition blocks on the canvas to fill empty "
            "space or arrange blocks side-by-side. The canvas is a "
            "Bootstrap-style grid whose width depends on the device: "
            "12 cols on desktop, 8 cols on tablet, 4 cols on phone. "
            "Rows are always 9. Pass an array of layouts "
            "`[{block_id, x, y, w, h}, ...]` and every listed block "
            "reflows in place — no remount, no reload, PDFs stay on "
            "the same page. Read the `(at x:.. y:.. w:.. h:..)` "
            "annotations in CURRENTLY ON CANVAS to know each block's "
            "starting position. Common layouts on DESKTOP (12×9): "
            "full-bleed `{x:0,y:0,w:12,h:9}`; left-half "
            "`{x:0,y:0,w:6,h:9}`; right-half `{x:6,y:0,w:6,h:9}`; "
            "top-third `{x:0,y:0,w:12,h:3}`; bottom two-thirds "
            "`{x:0,y:3,w:12,h:6}`; thirds "
            "`{x:0,y:0,w:4,h:9} | {x:4,y:0,w:4,h:9} | {x:8,y:0,w:4,h:9}`. "
            "On TABLET (8×9) halve the desktop col counts; on PHONE "
            "(4×9) quarter them. Pass `device_class` to validate "
            "against the right grid — when omitted, validation uses "
            "the desktop bounds. Use this tool when a block is "
            "leaving empty space, the user wants two surfaces "
            "side-by-side, or the user explicitly asks to make "
            "something bigger or smaller."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "layouts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "block_id": {"type": "string"},
                            "x": {"type": "integer", "minimum": 0, "maximum": 11},
                            "y": {"type": "integer", "minimum": 0, "maximum": 8},
                            "w": {"type": "integer", "minimum": 1, "maximum": 12},
                            "h": {"type": "integer", "minimum": 1, "maximum": 9},
                        },
                        "required": ["block_id", "x", "y", "w", "h"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                },
                "target_device_id": {
                    "type": "string",
                    "description": "Optional UUID; reflow on this device only.",
                },
                "device_class": {
                    "type": "string",
                    "enum": ["phone", "tablet", "desktop"],
                    "description": (
                        "Which grid the layouts target. Read the "
                        "device_class of the canvas in CURRENTLY ON "
                        "CANVAS and pass it here. Omit to validate "
                        "against the desktop grid (12×9)."
                    ),
                },
            },
            "required": ["layouts"],
            "additionalProperties": False,
        },
        executor=_make_layout_blocks(user_id),
        domain=ToolDomain.CANVAS,
    )
