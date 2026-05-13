// Port of frontend/lib/gridConfig.ts. Rows fixed at 9 across all devices for
// LLM reasoning uniformity. Cols scale: phone=4, tablet=8, desktop=12.

import type { DeviceClass } from "../device/deviceClass";

export interface GridSize {
  cols: number;
  rows: number;
}

export const GRID_SIZES: Record<DeviceClass, GridSize> = {
  phone:   { cols: 4,  rows: 9 },
  tablet:  { cols: 8,  rows: 9 },
  desktop: { cols: 12, rows: 9 },
};

export function gridForDevice(device: DeviceClass): GridSize {
  return GRID_SIZES[device];
}

export interface GridCoords {
  x: number;
  y: number;
  w: number;
  h: number;
}

// Scale coords authored in desktop grid (12×9) to the active device's grid.
// Mirrors frontend/lib/gridConfig.ts:scaleGridForDevice exactly.
export function scaleGridForDevice(coords: GridCoords, device: DeviceClass): GridCoords {
  const target = GRID_SIZES[device];
  const desktop = GRID_SIZES.desktop;
  const colScale = target.cols / desktop.cols;

  const x = Math.max(0, Math.min(target.cols - 1, Math.round(coords.x * colScale)));
  const w = Math.max(1, Math.min(target.cols - x, Math.round(coords.w * colScale)));
  const y = Math.max(0, Math.min(target.rows - 1, Math.round(coords.y)));
  const h = Math.max(1, Math.min(target.rows - y, Math.round(coords.h)));
  return { x, y, w, h };
}
