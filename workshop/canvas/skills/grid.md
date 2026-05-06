# Canvas grid

The canvas is a **Bootstrap-style grid** whose width depends on the
device. Rows are always 9. Read the device class of each canvas in
`CURRENTLY ON CANVAS` (e.g. `device_class: desktop`) before reasoning
about coordinates.

| Device class | `cols` | `rows` | Notes                           |
|--------------|--------|--------|---------------------------------|
| `desktop`    | **12** | 9      | Bootstrap 12-col convention.    |
| `tablet`     | **8**  | 9      | Half-step down.                 |
| `phone`      | **4**  | 9      | Quarter of desktop.             |

A block's position is `(x, y, w, h)` in grid cells:
- `x ∈ [0, cols-1]`, `y ∈ [0, rows-1]`
- `w ≥ 1`, `h ≥ 1`
- `x + w ≤ cols`, `y + h ≤ rows`

When you call `layout_blocks`, pass `device_class` so the server
validates against the right grid.

## Common layouts

### Desktop (12×9)

| Layout         | `(x, y, w, h)`     |
|----------------|--------------------|
| Full-bleed     | `(0, 0, 12, 9)`    |
| Left half      | `(0, 0, 6, 9)`     |
| Right half     | `(6, 0, 6, 9)`     |
| Left two-thirds| `(0, 0, 8, 9)`     |
| Right third    | `(8, 0, 4, 9)`     |
| Top third      | `(0, 0, 12, 3)`    |
| Bottom 2/3     | `(0, 3, 12, 6)`    |
| Three columns  | `(0,0,4,9)` `(4,0,4,9)` `(8,0,4,9)` |

### Tablet (8×9)

| Layout         | `(x, y, w, h)`     |
|----------------|--------------------|
| Full-bleed     | `(0, 0, 8, 9)`     |
| Left half      | `(0, 0, 4, 9)`     |
| Right half     | `(4, 0, 4, 9)`     |
| Top third      | `(0, 0, 8, 3)`     |
| Bottom 2/3     | `(0, 3, 8, 6)`     |

### Phone (4×9)

| Layout         | `(x, y, w, h)`     |
|----------------|--------------------|
| Full-bleed     | `(0, 0, 4, 9)`     |
| Top half       | `(0, 0, 4, 4)`     |
| Bottom half    | `(0, 4, 4, 5)`     |

On phone there isn't enough horizontal room for side-by-side layouts —
prefer **stacked** placements (top/bottom or sequential rows).

## Blocks may overlap

Layering (front/back) is governed by mount order and explicit `raise`
actions — see `layering.md`. Overlap is most useful on desktop where
the surface is wide enough to expose multiple layered blocks; on
phone, prefer stacked layouts since there's little visible gap to
reveal anything underneath.
