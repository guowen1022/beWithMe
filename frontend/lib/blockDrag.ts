// Shared pointer-drag helper for grid blocks. Both source-eval blocks
// (`Block.tsx`) and React-component "system" blocks (`BlockShell.tsx`)
// route their drag handle's pointerdown through here so the snap math,
// magnetic overshoot, and dragController/blockLayout commits are all
// implemented in one place.

import type { DeviceClass } from "./device";
import { GRID_SIZES, type GridCoords } from "./gridConfig";
import { blockLayout, dragController } from "./blockLayout";

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

const OVERSHOOT_RATIO = 0.18;

interface StartDragOpts {
  id: string;
  /** The element that gets translate-transformed during drag. */
  wrapper: HTMLElement;
  /** The grid root used to compute cell pixel size. */
  surface: HTMLElement;
  /** Active device class — drives cells/row count. */
  device: DeviceClass;
  /** Pointer screen coords at drag start. */
  startClientX: number;
  startClientY: number;
  /** Block's current desktop-coord position (override or source default). */
  startCoords: GridCoords;
}

/**
 * Begin a drag. Attaches document-level pointermove/up/cancel listeners,
 * applies snap+overshoot transform to `wrapper`, updates `dragController`
 * with the live snap target, and on release commits the new coords to
 * `blockLayout`. Bounds-clamped so the block can't leave the desktop grid.
 */
export function startBlockDrag(opts: StartDragOpts): void {
  const { id, wrapper, surface, device, startClientX, startClientY, startCoords } = opts;
  const rect = surface.getBoundingClientRect();
  const { cols, rows } = GRID_SIZES[device];
  const cellW = rect.width / cols;
  const cellH = rect.height / rows;
  if (cellW <= 0 || cellH <= 0) return;

  // Drags update desktop-coord layout. On phone (4 cols) one device cell
  // == 3 desktop cells, so we scale the integer device-cell delta back up.
  const desktopColScale = GRID_SIZES.desktop.cols / cols;
  const desktopRowScale = GRID_SIZES.desktop.rows / rows;

  let lastTarget = startCoords;
  dragController.start(id, startCoords);

  const move = (ev: PointerEvent) => {
    const rawDx = ev.clientX - startClientX;
    const rawDy = ev.clientY - startClientY;

    const cellDxDevice = Math.round(rawDx / cellW);
    const cellDyDevice = Math.round(rawDy / cellH);
    const snapDx = cellDxDevice * cellW;
    const snapDy = cellDyDevice * cellH;

    const overshootX = (rawDx - snapDx) * OVERSHOOT_RATIO;
    const overshootY = (rawDy - snapDy) * OVERSHOOT_RATIO;
    wrapper.style.transform = `translate(${snapDx + overshootX}px, ${snapDy + overshootY}px)`;
    wrapper.style.zIndex = "999";
    wrapper.style.transition = "transform 90ms cubic-bezier(0.2, 0.8, 0.2, 1)";

    const target: GridCoords = {
      x: clamp(
        startCoords.x + cellDxDevice * desktopColScale,
        0,
        GRID_SIZES.desktop.cols - startCoords.w,
      ),
      y: clamp(
        startCoords.y + cellDyDevice * desktopRowScale,
        0,
        GRID_SIZES.desktop.rows - startCoords.h,
      ),
      w: startCoords.w,
      h: startCoords.h,
    };
    if (target.x !== lastTarget.x || target.y !== lastTarget.y) {
      lastTarget = target;
      dragController.update(target);
    }
  };

  const finish = () => {
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", finish);
    document.removeEventListener("pointercancel", finish);
    wrapper.style.transform = "";
    wrapper.style.zIndex = "";
    wrapper.style.transition = "";
    blockLayout.set(id, lastTarget);
    dragController.end();
  };

  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", finish);
  document.addEventListener("pointercancel", finish);
}
