"""point_arrow — teacher's tool for drawing an arrow between two blocks.

One full-canvas overlay block (`arrow-overlay`) holds the SVG. The
teacher invokes `point_arrow(from_block_id, to_block_id, label?)` and
the block re-renders the arrow whenever its `arrow` topic ticks.

Mechanics:
  1. Ensure `blocks/arrow-overlay.{js,md}` exists in the user's git
     workspace. The block code is a fixed template — the engineer
     doesn't need to be invoked.
  2. Mount it on the user's canvas if it isn't already (UIUpdate event +
     canvas_layout row).
  3. Publish `{from, to, label}` on the block's `arrow` topic. Sticky
     pub/sub means the block sees the value even if it mounted in the
     same SSE batch.

Pass `from_block_id == to_block_id == ""` to clear (publishes None).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.frontend_engineer import workspace as ws
from infra.contracts.ui import BlockMessage, BlockSource, UIUpdate
from infra.db import async_session
from infra.devices import registry as device_registry
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from infra.devices.canvas_layout import CanvasLayout
from infra.model.tools import ToolSpec


_ARROW_BLOCK_ID = "arrow-overlay"
_ARROW_TOPIC = "arrow"


_ARROW_BLOCK_JS = """\
({
  id: 'arrow-overlay',
  // Full-bleed overlay in DESKTOP coords (12×9); frontend rescales per device.
  grid: { x: 0, y: 0, w: 12, h: 9 },
  layer: 'overlay',
  z: 50,
  style: {
    pointerEvents: 'none',
    background: 'transparent',
    border: 'none',
  },
  subscribes: ['arrow'],
  run(root, bus, cleanup) {
    const SVGNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.style.position = 'absolute';
    svg.style.inset = '0';
    svg.style.pointerEvents = 'none';
    svg.style.overflow = 'visible';

    const defs = document.createElementNS(SVGNS, 'defs');
    const markerId = 'arrowhead-' + Math.random().toString(36).slice(2);
    const marker = document.createElementNS(SVGNS, 'marker');
    marker.setAttribute('id', markerId);
    marker.setAttribute('viewBox', '0 0 10 10');
    marker.setAttribute('refX', '9');
    marker.setAttribute('refY', '5');
    marker.setAttribute('markerWidth', '6');
    marker.setAttribute('markerHeight', '6');
    marker.setAttribute('orient', 'auto-start-reverse');
    const head = document.createElementNS(SVGNS, 'path');
    head.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
    head.setAttribute('fill', '#fbbf24');
    marker.appendChild(head);
    defs.appendChild(marker);
    svg.appendChild(defs);
    root.appendChild(svg);

    let current = null;

    function findBlockEl(id) {
      if (!id) return null;
      const candidates = document.querySelectorAll('[data-dynamic-surface] [data-block-id]');
      for (let i = 0; i < candidates.length; i++) {
        const el = candidates[i];
        if (el.getAttribute('data-block-id') === id) return el;
      }
      return null;
    }

    function clearDrawn() {
      const kids = Array.from(svg.children);
      for (let i = 0; i < kids.length; i++) {
        if (kids[i] !== defs) kids[i].remove();
      }
    }

    // Clip a ray from rect-center toward (tx, ty) to where it exits the
    // rect's perimeter. Returns {x, y} on the boundary nearest the target.
    // The ray is parameterised t ∈ [0, ∞); the smallest t at which the
    // ray crosses a vertical or horizontal edge wins.
    function rayExit(cx, cy, hw, hh, tx, ty) {
      const dx = tx - cx;
      const dy = ty - cy;
      if (dx === 0 && dy === 0) return { x: cx, y: cy };
      const txEdge = dx !== 0 ? hw / Math.abs(dx) : Infinity;
      const tyEdge = dy !== 0 ? hh / Math.abs(dy) : Infinity;
      const t = Math.min(txEdge, tyEdge);
      return { x: cx + t * dx, y: cy + t * dy };
    }

    function render() {
      clearDrawn();
      if (!current) return;
      const fromEl = findBlockEl(current.from);
      const toEl = findBlockEl(current.to);
      if (!fromEl || !toEl) return;
      const svgRect = svg.getBoundingClientRect();
      const fr = fromEl.getBoundingClientRect();
      const tr = toEl.getBoundingClientRect();
      const fcx = fr.left + fr.width / 2 - svgRect.left;
      const fcy = fr.top + fr.height / 2 - svgRect.top;
      const tcx = tr.left + tr.width / 2 - svgRect.left;
      const tcy = tr.top + tr.height / 2 - svgRect.top;

      // Clip the ray to each block's edge so the line starts and ends at
      // the content boundary, not at the (hidden) center. A small gap
      // pushes the arrowhead just outside the target so it visually sits
      // beside the content rather than touching it.
      const GAP = 6;
      const start = rayExit(fcx, fcy, fr.width / 2, fr.height / 2, tcx, tcy);
      const endRaw = rayExit(tcx, tcy, tr.width / 2, tr.height / 2, fcx, fcy);
      const ex = endRaw.x - tcx;
      const ey = endRaw.y - tcy;
      const elen = Math.hypot(ex, ey) || 1;
      const end = {
        x: endRaw.x + (ex / elen) * GAP,
        y: endRaw.y + (ey / elen) * GAP,
      };

      const line = document.createElementNS(SVGNS, 'line');
      line.setAttribute('x1', String(start.x));
      line.setAttribute('y1', String(start.y));
      line.setAttribute('x2', String(end.x));
      line.setAttribute('y2', String(end.y));
      line.setAttribute('stroke', '#fbbf24');
      line.setAttribute('stroke-width', '3');
      line.setAttribute('stroke-linecap', 'round');
      line.setAttribute('marker-end', 'url(#' + markerId + ')');
      svg.appendChild(line);

      if (current.label) {
        const text = document.createElementNS(SVGNS, 'text');
        text.setAttribute('x', String((start.x + end.x) / 2));
        text.setAttribute('y', String((start.y + end.y) / 2 - 8));
        text.setAttribute('fill', '#fbbf24');
        text.setAttribute('font-family', 'ui-sans-serif, system-ui, sans-serif');
        text.setAttribute('font-size', '14');
        text.setAttribute('font-weight', '600');
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('paint-order', 'stroke');
        text.setAttribute('stroke', '#0a0a0a');
        text.setAttribute('stroke-width', '4');
        text.textContent = String(current.label);
        svg.appendChild(text);
      }
    }

    const unsub = bus.subscribe('arrow', (value) => {
      if (!value || !value.from || !value.to) {
        current = null;
      } else {
        current = { from: value.from, to: value.to, label: value.label || null };
      }
      render();
    });
    cleanup(() => unsub());

    const onResize = () => render();
    window.addEventListener('resize', onResize);
    cleanup(() => window.removeEventListener('resize', onResize));

    // Other blocks may mount/unmount around us without firing a resize.
    // A 500ms repaint is cheap (clearDrawn early-returns when current is
    // null) and keeps the arrow pinned to its anchors.
    const interval = setInterval(render, 500);
    cleanup(() => clearInterval(interval));
  },
})
"""

_ARROW_BLOCK_MD = (
    "# arrow-overlay\n\n"
    "Full-canvas overlay block owned by the teacher's `point_arrow` tool. "
    "Subscribes to topic `arrow` (value: `{from, to, label?}` or null) and "
    "draws an SVG line + arrowhead between the two named blocks. "
    "Pointer-events disabled so it never intercepts user clicks.\n"
)


def _ensure_arrow_block_in_workspace(user_id: UUID) -> BlockSource:
    """Write blocks/arrow-overlay.{js,md} if missing. Always returns the
    BlockSource so callers can ship it as a mount event."""
    snap = ws.read_snapshot(user_id)
    existing = snap.blocks.get(_ARROW_BLOCK_ID)
    if existing is None or existing.js != _ARROW_BLOCK_JS:
        ws.write_files(
            user_id,
            [
                ws.FileWrite(path=f"blocks/{_ARROW_BLOCK_ID}.js", content=_ARROW_BLOCK_JS),
                ws.FileWrite(path=f"blocks/{_ARROW_BLOCK_ID}.md", content=_ARROW_BLOCK_MD),
            ],
        )
        ws.regenerate_topics(user_id)
        ws.commit(user_id, "tools.point_arrow: install arrow-overlay block")
    return BlockSource(id=_ARROW_BLOCK_ID, source=_ARROW_BLOCK_JS, design_doc=_ARROW_BLOCK_MD)


async def _online_device_ids(user_id: UUID) -> list[UUID]:
    devices = await device_registry.list_for_user(user_id)
    return [d.device_id for d in devices if d.online]


async def _record_mount(user_id: UUID, block_id: str, device_ids: list[UUID]) -> None:
    if not device_ids:
        return
    async with async_session() as session:
        for did in device_ids:
            stmt = (
                pg_insert(CanvasLayout)
                .values(user_id=user_id, device_id=did, block_id=block_id)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "device_id", "block_id"],
                )
            )
            await session.execute(stmt)
        await session.commit()


async def point_arrow(
    *,
    user_id: UUID,
    from_block_id: str,
    to_block_id: str,
    label: Optional[str] = None,
    target_device_id: Optional[UUID] = None,
) -> dict:
    """Mount the arrow-overlay block (idempotent) and publish the arrow.

    Pass empty strings for both block ids to hide the arrow.
    """
    block = _ensure_arrow_block_in_workspace(user_id)

    mount_event = UIUpdate(action="mount", block=block)
    if target_device_id is not None:
        mount_targets = [target_device_id]
        delivered_mount = await enqueue_for_device(user_id, target_device_id, mount_event)
    else:
        mount_targets = await _online_device_ids(user_id)
        delivered_mount = await enqueue_for_user(user_id, mount_event)
    await _record_mount(user_id, _ARROW_BLOCK_ID, mount_targets)

    payload: Optional[dict]
    if from_block_id and to_block_id:
        payload = {"from": from_block_id, "to": to_block_id}
        if label:
            payload["label"] = label
    else:
        payload = None

    arrow_event = BlockMessage(block_id=_ARROW_BLOCK_ID, topic=_ARROW_TOPIC, value=payload)
    if target_device_id is not None:
        delivered_arrow = await enqueue_for_device(user_id, target_device_id, arrow_event)
    else:
        delivered_arrow = await enqueue_for_user(user_id, arrow_event)

    return {
        "block_id": _ARROW_BLOCK_ID,
        "delivered_mount": delivered_mount,
        "delivered_arrow": delivered_arrow,
        "cleared": payload is None,
    }


__all__ = ["point_arrow", "build_spec"]

def _make_point_arrow(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        from_id = (args.get("from_block_id") or "").strip()
        to_id = (args.get("to_block_id") or "").strip()
        # Allow both empty to mean "clear the arrow".
        if (bool(from_id) ^ bool(to_id)):
            return json.dumps({"error": "from_block_id and to_block_id must both be set, or both empty to clear"})
        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})
        result = await point_arrow(
            user_id=user_id,
            from_block_id=from_id,
            to_block_id=to_id,
            label=args.get("label"),
            target_device_id=target_uuid,
        )
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="point_arrow",
        description=(
            "Draw an arrow on the canvas pointing from one block to another, "
            "with an optional label. Use to visually link two ideas the user "
            "is comparing or to direct attention from a question to its "
            "answer. Pass both ids empty to clear a previously-drawn arrow."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "from_block_id": {
                    "type": "string",
                    "description": "Source block id (the arrow's tail).",
                },
                "to_block_id": {
                    "type": "string",
                    "description": "Target block id (the arrow's head).",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label rendered near the midpoint.",
                },
                "target_device_id": {"type": "string"},
            },
            "required": ["from_block_id", "to_block_id"],
            "additionalProperties": False,
        },
        executor=_make_point_arrow(user_id),
    )
