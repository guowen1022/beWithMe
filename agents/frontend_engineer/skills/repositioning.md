---
name: repositioning
keywords: move, reposition, shift, drag, slide, resize, bigger, smaller, wider, taller, narrower, shorter, below, above, beside, left, right, top, bottom, center, lower, higher
when: Move or resize an existing block. Body untouched — only the grid changes.
---

# Repositioning

Operation: move an existing block on the canvas. **Do not change its
body.** The body — styles, content, run() — already obeys the project's
layout/font/behavior contracts. Touching it is the failure mode this
operation is designed to prevent.

## Order of work (inherits `principles.md`)

1. **Read** the workspace dump. Find the block by id. Note its current
   `grid: { x, y, w, h }`.
2. **Collect** — irrelevant for this operation; no template needs to
   match.
3. **Write** the smallest grid change that satisfies the request.

## Today's protocol (full re-emit)

Until the partial-edit verb (`### grid blocks/<id>`) lands, repositioning
requires re-emitting the whole `<id>.js`. This is dangerous — every
re-emit risks corrupting the body. To stay safe:

- Copy the existing `.js` body verbatim from the workspace dump.
- Change *only* the four numbers inside `grid: { x, y, w, h }`.
- Do not "improve" anything else. Not the styles, not the cleanup
  registrations, not the topic names, not the comments.
- Do not touch the `.md` unless the design intent of the placement
  changed (e.g. "moved to top-right HUD position" is worth recording).

If the user asks for a relative move ("down 10 rows"), compute the new
absolute coordinates from the current grid and emit those.

## Planned partial-edit verb

When `### grid blocks/<id>` lands, repositioning becomes a 4-number
patch the parser merges into the on-disk source. No body re-emission,
no risk to the template-faithful code. The skill body stays the same;
only the protocol shape changes.

## Grid rules (the canvas)

Author block grids in **DESKTOP coords (12 columns × 9 rows)**. The
frontend rescales for tablet (8 cols) and phone (4 cols) on render —
you don't need to think about smaller breakpoints when emitting the
block source.

- `x`, `y` are 0-indexed (top-left corner).
- `w`, `h` are span counts; on desktop `x + w ≤ 12`, `y + h ≤ 9`.
- Cells are non-square (1.78:1 on a 16:9 canvas). A square-looking
  block has `w ≈ 0.56 × h` (e.g. 4 cols × 7 rows).
- Blocks must not overlap unless one is `layer: 'overlay'` (overlay
  always renders above canvas blocks regardless of z-index).

### Layout rules

- Don't pin everything to the very edge — leave a column of margin
  when content is narrow enough.
- Don't crowd. If two blocks touch, leave a single empty cell of
  separation when there's room.
- Wide content (PDFs, tables, code): `w ≥ 8`. Narrow controls
  (buttons, inputs): `w ≤ 6`.
- Tall content (long lists, scrollable panels): `h ≥ 5`. Status chips
  and toolbars: `h ≤ 2`.

### Canonical placements (desktop coords)

For the most common composites — use these instead of inventing:

- **Upload bar (top center)**: `{ x: 3, y: 1, w: 6, h: 1 }`
- **PDF reader (full bleed)**: `{ x: 0, y: 0, w: 12, h: 9 }`
- **Side-by-side halves**: `{ x: 0, y: 0, w: 6, h: 9 }` + `{ x: 6, y: 0, w: 6, h: 9 }`
- **Single centered block**: `{ x: 2, y: 2, w: 8, h: 5 }`

### Layer

- `layer: 'canvas'` (default) — normal in-grid block.
- `layer: 'overlay'` — always above canvas blocks. Use for
  notifications, mode indicators, transient HUD elements.

## What this operation never does

- Change `id` (that would be a delete + new block, not a reposition).
- Change `subscribes` / `publishes` (that would change the wiring).
- Change `style`, `content`, or `run()` body.
- Re-order keys in the object literal "for cleanliness".

If the user actually wants any of those, that's a different operation
— either a new block or a delete-and-readd. Confirm in the plan line
before doing it.
