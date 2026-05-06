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
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user


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


__all__ = ["layout_blocks"]
