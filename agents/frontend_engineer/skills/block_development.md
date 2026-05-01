# Block development

A **block** is a parens-wrapped JavaScript object literal that the runtime
evaluates and mounts on a 160×90 grid canvas. The browser is the sandbox.

## Schema (every field is required unless marked optional)

```js
({
  id: 'kebab-id',                       // unique among the user's blocks
  grid: { x: 0..159, y: 0..89,          // top-left corner
          w: 1..160,  h: 1..90 },        // size; x+w ≤ 160, y+h ≤ 90
  content: 'static text shown on mount', // can be empty string
  style: { /* React-shaped CSS, camelCase */ },
  layer:    'canvas' | 'overlay',       // optional; default 'canvas'
  z:        0..999,                     // optional; default 0
  subscribes: ['topic1', 'topic2'],     // optional; topics this block reads
  publishes:  ['topic3'],                // optional; topics this block writes
  run(root, bus, cleanup) {
    // root: HTMLDivElement assigned to this block
    // bus: BlockBus with { publish(topic, value), subscribe(topic, fn) → unsub }
    // cleanup: register a teardown callback
  },
})
```

## Hard rules

- No `import`, no `require`, no top-level `await`. The block source is
  evaluated as `new Function('return (' + source + ');')()` — anything that
  isn't a valid expression body is a syntax error.
- The block file is JUST the parens-wrapped object expression. Nothing
  before it, nothing after it, no exports, no comments outside the object.
- `id` must be lowercase kebab-case (`/^[a-z0-9][a-z0-9-]*$/`).
- DOM mutations must go through `root` (your assigned `<div>`). Don't query
  or mutate global selectors.
- Every listener / interval / observer / fetch-controller you start MUST be
  registered with `cleanup(() => …)`. The runtime unmounts blocks by id;
  leaks accumulate.
- `style` keys are camelCase (`backgroundColor`, not `background-color`).

## Available globals

- `localStorage` — has `bewithme_user_id` set; use it for `X-User-Id`
  headers when making API calls.
- `fetch` — for any backend call. The frontend proxies `/api/...` to the
  backend; never use absolute URLs.
- `window.pdfjsLib` — pdf.js v4+ loaded globally. Use `getDocument(...)`
  and `new pdfjsLib.TextLayer(...)` for PDF rendering. (The runtime
  pre-loads it; you don't need to import.)

## Templates

Reference patterns live at `frontend/templates/blocks/`. They cover tricky
cases (file upload, PDF rendering with text-layer + selection) so you
don't have to pick a library or get the boilerplate right. Use them as a
starting point when applicable; copy the relevant chunks and adapt the id,
grid, topics. You don't have to use a template — write fresh code if it
fits the user better.
