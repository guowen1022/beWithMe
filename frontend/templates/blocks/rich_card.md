---
keywords: explanation, definition, comparison, walkthrough, diagram, illustration, wiki, lesson, breakdown, deep-dive
purpose: "The teacher's standard explanation surface. Persona authors HTML in a constrained grammar (containers, type scale, tone classes, embedded Mermaid diagrams) and the backend pre-renders diagrams to SVG + sanitizes against the allowlist. Use for any explanation longer than a sentence."
subscribes:
  - __CONTENT_TOPIC__
grid:
  x: 1
  y: 1
  w: 10
  h: 8
---

Use `rich_card` whenever you're *explaining* something — definitions,
comparisons, walkthroughs, illustrated concepts. It's the Wikipedia-like
surface: a structured card with prose, embedded diagrams, images, and
inline annotation that renders identically on web and mobile.

`text_display` is still the right choice for one- or two-sentence
answers and voice transcripts (cheaper tokens, simpler).

## What you author

Pass `params.content` as an HTML string conforming to the rich_card
grammar (`infra/render/rich_card_grammar.py`). The backend sanitizes,
resolves Mermaid sources, and inlines SVGs before either web or mobile
sees the bytes.

### Worked example

```html
<div class="card card-hero">
  <h2 class="t-display">Quicksort</h2>
  <p>Divide-and-conquer with a <mark>pivot</mark>.</p>
  <div class="bw-diagram" data-src="graph TD; A[unsorted]-->B[pivot]; B-->C[left]; B-->D[right]"></div>
  <p class="t-body">Recurse on each half until sorted.</p>
  <ul>
    <li>Best: <span class="success">O(n log n)</span></li>
    <li>Worst: <span class="danger">O(n²)</span></li>
  </ul>
</div>
```

### Grammar reference

- **Containers**: `card`, `card-hero`, `card-callout`, `card-compare`,
  `card-timeline`, `card-definition`, `row`, `col`, `gap-{sm,md,lg}`,
  `pad-{sm,md,lg}`.
- **Tone**: `accent`, `accent-soft`, `muted`, `danger`, `warn`, `success`,
  `info`.
- **Type scale**: `t-display`, `t-title`, `t-body`, `t-caption`, `t-mono`,
  `weight-bold`, `weight-semi`, `italic`.
- **Annotation**: `<mark>`, `<ins>`, `<del>`, `revision-add`,
  `revision-remove`, `revision-changed`.
- **Media**: `<div class="bw-diagram" data-src="<mermaid source>"></div>`
  for diagrams; `<img class="bw-image aspect-16-9" src="https://..."
  alt="...">` for images (https only).
- **Layout helpers**: `center`, `right`, `border`, `border-top`,
  `border-bottom`, `round`, `round-lg`.

Anything outside this set is stripped. No `<script>`, no `<iframe>`, no
inline `style=""`, no `http://` URLs, no `<table>` (use lists or
`card-compare`).

## How to update in place

Push to `__CONTENT_TOPIC__` (per-block topic `text.<block_id>.content`)
via `push_block_content`. The new HTML is preprocessed the same way and
replaces the body without remounting — scroll position is preserved.

## State reporting

Emits `kind: "rich"` with the leading 200 chars of plaintext plus
`extra.{diagram_count, image_count, char_count, selection}` so
`read_media` reflects what's visible.
