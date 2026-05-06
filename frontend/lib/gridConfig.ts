// Single source of truth for canvas grid dimensions per device class.
//
// The dynamic surface is a CSS grid sized in cells, not pixels. Each cell
// is `1fr` of the surface, so the grid is always proportional — what
// changes per device is the *resolution* (12 cols on desktop, 8 on tablet,
// 4 on phone). Bootstrap-style 12-col on desktop matches the LLM's strong
// prior for col-6/col-4/col-3 layout reasoning while still mapping cleanly
// onto smaller breakpoints (4 → 8 → 12 cascade).
//
// Rows stay at 9 across all device classes so the canvas keeps a 16:9-ish
// aspect ratio and vertical reasoning is uniform regardless of width.

import type { DeviceClass } from "./device";

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

/**
 * Read the active grid size from the dynamic surface's `data-device`
 * attribute. Used by stateless modules (e.g. dynamicBlockRegistry) that
 * don't have access to the React `useDeviceClass()` hook. Falls back to
 * desktop when no surface is mounted yet, which is the safest default —
 * setGrid() called too early simply uses larger bounds.
 */
export function readGridSizeFromDom(): GridSize {
  if (typeof document === "undefined") return GRID_SIZES.desktop;
  const surface = document.querySelector<HTMLElement>("[data-dynamic-surface]");
  const device = surface?.getAttribute("data-device") as DeviceClass | null;
  return GRID_SIZES[device ?? "desktop"];
}

export interface GridCoords {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Scale grid coords authored on the desktop grid (12×9) to the active
 * device's grid. Block templates declare a single canonical grid in
 * desktop coords; the frontend rescales on render so one source works
 * across all breakpoints.
 *
 * Columns scale proportionally to the device's `cols`. Rows are 9 across
 * every device class (see GRID_SIZES) so rows pass through unchanged.
 *
 * Rounding can leave small gaps on phone — e.g. three desktop columns of
 * width 4 each scale to phone widths of 1.33→1, summing to 3 instead of
 * 4. Accept this for v1; the teacher can call `layout_blocks` with
 * explicit per-device coords when precise alignment matters.
 */
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
