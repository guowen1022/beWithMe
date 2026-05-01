# Grid positioning

The canvas is a fixed **160 columns × 90 rows** CSS grid. Every block
occupies a rectangle on this grid via `grid: { x, y, w, h }`.

- `x`, `y` are 0-indexed (top-left corner).
- `w`, `h` are span counts; `x + w ≤ 160`, `y + h ≤ 90`.
- Blocks should never overlap unless one is `layer: 'overlay'` (which is
  rendered above all `layer: 'canvas'` blocks regardless of z-index).

## Aspect ratio

The grid is roughly 16:9 — each cell is wider than it is tall in pixels.
A square-looking block has `w ≈ 1.78 × h`. Plan accordingly.

## Canonical placements

When the user wants a paired upload + reader (the most common composite),
use these grids:

- **Upload bar (top center)**: `{ x: 50, y: 6, w: 60, h: 12 }` — short
  card across the upper third.
- **PDF reader (below)**: `{ x: 20, y: 22, w: 120, h: 60 }` — large
  reading surface, leaves margin.

For a single centered block: `{ x: 40, y: 28, w: 80, h: 30 }`.

## Layout rules

- Leave at least 4 cells of margin from each canvas edge — the surface
  has rounded corners and shadows that look bad against the edge.
- Don't crowd. If two blocks touch, separate them by ≥2 cells (rows or
  columns) so the user can tell them apart visually.
- Wide content (PDFs, tables, code) gets w ≥ 100. Narrow controls
  (buttons, inputs) get w ≤ 60.
- Tall content (long lists, scrollable panels) gets h ≥ 40. Status chips
  and toolbars get h ≤ 14.

## Layer

- `layer: 'canvas'` (default) — normal in-grid block.
- `layer: 'overlay'` — always renders above canvas blocks. Use for
  notifications, mode indicators, transient HUD elements.
