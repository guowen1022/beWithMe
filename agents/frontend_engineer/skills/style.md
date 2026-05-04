---
name: style
always: true
when: Visual contract — every emitted block must conform. Read before writing styles.
---

# Style — block design system

These rules make every block on the canvas look like one product instead
of a quilt. Templates already conform; they're the safest starting point
(see `new_block.md` Path A). Apply this file when you handwrite (Path C)
or modify a template's surface styling.

## Tokens

Defined as CSS variables on `:root` in `frontend/app/globals.css`. Use
`var(--bw-*)` inside block styles — never paste the hex values, so a
single token edit re-themes every block.

| token | value | use |
|---|---|---|
| `--bw-void` | `#07070C` | page-level background (rare in a block) |
| `--bw-surface` | `#12121E` | block body / card background |
| `--bw-surface-2` | `#1A1A2A` | block header strip, inset chips |
| `--bw-border` | `#262638` | hairline (idle) |
| `--bw-border-strong` | `#3A3A52` | hairline (chrome / device frame) |
| `--bw-ink` | `#F4F4F8` | primary text |
| `--bw-ink-muted` | `#9090A8` | body / metadata |
| `--bw-ink-faint` | `#4F4F66` | placeholder / tertiary |
| `--bw-accent` | `#5C8CE6` | the **only** accent. used sparingly. |
| `--bw-accent-soft` | `rgba(92,140,230,0.16)` | accent fill, hover bg, active chip |
| `--bw-font-sans` | Onest → fallback chain | default body / titles |
| `--bw-font-serif` | Fraunces (italic only) | inline emphasis / quotes |
| `--bw-font-mono` | JetBrains Mono → fallback | id chips, technical values |

There is exactly **one** accent. Don't introduce a second.

## Block anatomy

Every block is a sharp rectangle: header strip + body. Apply these styles
at the descriptor level (`style:`) and inside `run()` for child elements.

```js
({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  style: {
    background:    'var(--bw-surface)',
    color:         'var(--bw-ink)',
    border:        '1px solid var(--bw-border)',
    borderRadius:  '0',                          // sharp — no rounding
    fontFamily:    'var(--bw-font-sans)',
    overflow:      'hidden',
    display:       'flex',
    flexDirection: 'column',
  },
  run(root, bus, cleanup, helpers) {
    // ── header strip ─────────────────────────────
    var head = document.createElement('div');
    head.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'ROLE-NAME';            // role, not block id
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var title = document.createElement('span');
    title.textContent = 'Block Title';
    title.style.cssText =
      'flex:1; font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink); white-space:nowrap;' +
      'overflow:hidden; text-overflow:ellipsis;';

    head.append(idChip, title);
    root.appendChild(head);

    // ── body ─────────────────────────────────────
    var body = document.createElement('div');
    body.style.cssText =
      'flex:1; padding:14px; overflow:auto;' +
      'font-size:11px; line-height:1.55;' +
      'color:var(--bw-ink-muted);';
    root.appendChild(body);

    // ...your block content into `body`...
  },
})
```

### id chip

Mono, 9.5px, uppercase, letter-spacing 0.08em, accent text on
accent-soft fill. Padding `3px 8px`. No border, no radius. Shows the
block's *role* (e.g. `PDF-READER`, `PASSAGE`, `CONCEPT-MAP`) — not its
unique id.

### title

Sans, 600, 11.5px, ink color, ellipsis on overflow.

### body padding

Default `14px`. Use less only when the body is a wrapper around an
already-padded inner card.

## Typography

| context | font | weight | size |
|---|---|---|---|
| block title | sans | 600 | 11.5px |
| body text | sans | 400 | 10–11px |
| heading inside body | sans | 700 | 13–16px |
| numeric / technical metadata | mono | 400 | 10px |
| id chip / status label | mono | 500 | 9.5px |
| **italic emphasis** | **serif** | 400 | inherit, italic |

Serif is voice-only (Fraunces italic, opsz axis). Never lead a block
with serif. Default everything to sans.

## Buttons

Solid accent, cool off-white text, no radius:

```js
btn.style.cssText =
  'background:var(--bw-accent); color:#E8EEFA;' +
  'padding:8px 14px; border:none; border-radius:0;' +
  'font-family:var(--bw-font-mono); font-size:11px;' +
  'letter-spacing:.1em; text-transform:uppercase; cursor:pointer;';
```

For a quiet / secondary action: ghost — transparent bg, hairline border,
ink text.

## Inputs

Hairline border, surface-2 background, ink text. Sharp corners. No focus
glow — flip border to `var(--bw-accent)` on `:focus` instead.

## Hover

Borders shift to `var(--bw-accent)`. Don't add gradients, glows, or
scale transforms on inner elements.

## Internal layout (when a block hosts sub-cells)

The outer canvas is a **160×90** grid (see `new_block.md` schema —
`grid: { x: 0..159, y: 0..89 }`); blocks claim chunks of it. *Inside* a
block, treat the body as a fresh 12-col grid with a 14px gap when laying
out sub-content:

```css
display: grid;
grid-template-columns: repeat(12, 1fr);
gap: 14px;
```

A child spanning N cols is `grid-column: span N`. Common splits: 12,
6+6, 8+4, 7+5, 4+4+4, 6×2.

## Don't

1. Don't introduce a second accent color — only `--bw-accent`.
2. Don't round inner elements — `border-radius: 0` everywhere.
3. Don't paste hex literals — use `var(--bw-*)` so the system stays portable.
4. Don't use pure white (`#FFF`) for text — use `var(--bw-ink)`.
5. Don't add drop shadows on inner elements — hairlines do the work.
6. Don't lead with serif — italic Fraunces is for emphasis only.
7. Don't import fonts inside the block — they're loaded by the host.

## Do

1. Favor `var(--bw-*)` over hex literals.
2. Keep the header ~36px tall (`padding: 9px 12px`).
3. Use mono for any numeric / id-like / technical value.
4. Match the canvas grid gap (14px) for nested grids.
5. When in doubt, copy the anatomy block above and only swap the body.
