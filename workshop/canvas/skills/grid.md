# Canvas grid

The canvas is a **160-wide × 90-tall** grid of cells (not pixels). The
top-left cell is `(0, 0)`; the bottom-right is `(159, 89)`.

Every block on the canvas has a position `(x, y, w, h)` in grid cells:
- `x ∈ [0, 159]`, `y ∈ [0, 89]`
- `w ≥ 1`, `h ≥ 1`
- `x + w ≤ 160`, `y + h ≤ 90`

Common layouts:

| Layout         | `(x, y, w, h)`           |
|----------------|--------------------------|
| Full-bleed     | `(0, 0, 160, 90)`        |
| Left half      | `(0, 0, 80, 90)`         |
| Right half     | `(80, 0, 80, 90)`        |
| Top third      | `(0, 0, 160, 30)`        |
| Bottom 2/3     | `(0, 30, 160, 60)`       |
| Top-left quad  | `(0, 0, 80, 45)`         |

Blocks may overlap; layering (front/back) is governed by mount order
and explicit `raise` actions — see `layering.md`.
