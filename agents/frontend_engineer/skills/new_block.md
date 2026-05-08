---
name: new_block
keywords: add, create, build, draw, render, generate, attach, show me, let me, give me, i want, i need
needs_templates: true
when: Add a new block to the canvas. Template-first; handwrite only as fallback.
---

# New block

Operation: add a block to the user's canvas.

## Order of work (inherits `principles.md`)

1. **Read** the workspace dump above. If a block already on the canvas
   covers the request, say so in your plan and stop. No new block.
2. **Collect** templates whose `keywords:` line matches the user's
   request. Score by overlap. If the best match is strong, use it.
3. **Write** only when no template matches. Handwriting is the fallback,
   not the default.

## Path A — copy a matching template

This is the preferred path. Templates already obey the project's
layout, fonts, color, motion, and behavior contracts. Don't reinvent.

1. Pick the template (e.g. `upload_file`, `pdf_reader`).
2. Copy the `.js` source verbatim, then adapt placeholders:
   - `__BLOCK_ID__` → kebab-case id unique within the user's workspace.
   - `__GRID_X__`, `__GRID_Y__`, `__GRID_W__`, `__GRID_H__` →
     numbers from `repositioning.md` (use the canonical placements when
     applicable; otherwise pick by content size).
   - `__DOC_TOPIC__` (and similar) → a kebab-case topic name. If two
     templates need to share, pick the same string for both.
3. Write the matching `<id>.md` describing purpose and topics.
4. Do NOT change anything else inside the template — not styles, not
   structure, not function names. The template is the contract.

## Path B — compose two templates

When the request needs producer + consumer (the canonical case is
"upload a doc and read it"), use both templates. They must agree on a
shared topic.

- `upload_file` publishes on `__DOC_TOPIC__` → use `uploaded_doc`.
- `pdf_reader` subscribes to that same topic → use `uploaded_doc`.
- The reader publishes selection on `<topic>_selection` →
  `uploaded_doc_selection`.

Place them per `repositioning.md` canonical placements (upload bar top,
reader below). Don't crowd; ≥2 cells of separation.

## Path C — handwrite (only if A and B fail)

If no template matches, write a block from scratch. Keep it minimal —
the schema is the contract, and the runtime is unforgiving.

### Schema (every field required unless marked optional)

```js
({
  id: 'kebab-id',                       // unique among the user's blocks
  grid: { x: 0..11, y: 0..8,            // top-left corner (DESKTOP coords;
          w: 1..12,  h: 1..9 },          //  scaled to tablet/phone on render)
  content: 'static text shown on mount', // can be empty string
  style: { /* React-shaped CSS, camelCase */ },
  layer:    'canvas' | 'overlay',       // optional; default 'canvas'
  z:        0..999,                     // optional; default 0
  subscribes: ['topic1', 'topic2'],     // optional; topics this block reads
  publishes:  ['topic3'],                // optional; topics this block writes
  run(root, bus, cleanup) {
    // root: HTMLDivElement assigned to this block
    // bus: { publish(topic, value), subscribe(topic, fn) → unsub }
    // cleanup: register a teardown callback
  },
})
```

### Hard rules

- No `import`, no `require`, no top-level `await`. The block source is
  evaluated as `new Function('return (' + source + ');')()` — anything
  that isn't a valid expression body is a syntax error.
- The block file is JUST the parens-wrapped object. Nothing before it,
  nothing after it, no exports, no comments outside the object.
- `id` must match `/^[a-z0-9][a-z0-9-]*$/`.
- DOM mutations go through `root` only. Never query global selectors.
- Every listener / interval / observer / fetch-controller MUST be
  registered with `cleanup(() => …)`. The runtime unmounts blocks by
  id; leaks accumulate across turns.
- `style` keys are camelCase (`backgroundColor`, not `background-color`).

### Available globals

- `localStorage` — has `bewithme_user_id`. Use it for `X-User-Id`
  headers when calling the backend.
- `fetch` — for any backend call. The frontend proxies `/api/...`;
  never use absolute URLs.
- `window.pdfjsLib` — pdf.js v4+ pre-loaded. Use `getDocument(...)` and
  `new pdfjsLib.TextLayer(...)` for PDFs.

### Helpers (4th arg of `run`)

`run(root, bus, cleanup, helpers)` — the host injects a `helpers` object:

- `helpers.reportState({kind, content, extra, completed?})` — emit a
  structured state report so the perception cache surfaces it. Prefer
  this over relying on the auto-DOM-snapshot.
- `helpers.backend.<name>(args)` — auto-generated typed backend caller.
  Available when the block's `.md` declares a `backend:` map.
- `helpers.blockId` — the block's id (for log lines, etc.).
- `helpers.audio.{startVad, transcribe, stopAll}` — mic + STT, gated
  through the shared mic arbiter. Only needed for mic blocks.
- `helpers.markdown(text)` — render a GFM markdown string to an HTML
  string. Use this for any prose/note rendering rather than hand-rolling
  a parser. Tables, fenced code, blockquotes, and headings all work.
  `marked` is configured once in the host with `gfm + breaks` and is
  HTML-escape-safe by default. Pattern:
  ```js
  body.className = 'bw-prose';                   // typography contract
  body.innerHTML = helpers.markdown(text);
  ```
  The container **must** carry `class="bw-prose"` — see `style.md` →
  *Markdown surfaces*. Without it Tailwind's preflight strips heading
  hierarchy and tables look like a wall of identical text.

## Bus wiring (cross-block coordination)

Blocks coordinate over a sticky pub/sub bus. Inside `run`:

```js
bus.publish('uploaded_doc', { id, title, pages });
const unsub = bus.subscribe('uploaded_doc', (payload) => { /* ... */ });
cleanup(() => unsub());
```

- Topic names are kebab-case and describe the **data**, not the source
  block. Good: `uploaded_doc`, `selected_text`. Bad: `upload_output`.
- Selection convention: `<topic>_selection` for "user selected text
  inside <topic>".
- Declare every topic this block reads/writes via `subscribes` and
  `publishes` arrays. The runtime auto-regenerates `TOPICS.md` after
  every commit.
- Before writing a new block that needs data from another, check
  `TOPICS.md`. If a block already publishes the topic you need,
  subscribe instead of duplicating the source.
