# Canvas tool verbs

The verbs available for driving the canvas. Each operates on the
device-class grid (12×9 desktop / 8×9 tablet / 4×9 phone — see
`grid.md`) and the block lifecycle described in the sibling skills.

- **`read_media`** — read what the user currently sees: every canvas's
  mounted blocks (each block's id, title, current state, focus, grid
  position) and every voice device (with recent utterances). Pass no
  arguments to read everything; pass `block_ids` / `device_ids` to
  filter. Cheap enough to call every turn.

- **`read_document`** — actively read content from a PDF that's loaded
  in `pdf_reader`. Three actions:
  - `action='outline'` — table of contents + page count.
  - `action='page', page=N` — full text of page N (1-indexed).
  - `action='query', query='...'` — vector search across the doc; results carry `page_number`.

- **`mount_template`** — display a known reading surface. Templates:
  `upload_file` (PDF picker), `passage_reader` (paste/type text),
  `pdf_reader` (rendered PDF), `inputs_launcher` (two-button starter,
  rarely needed manually). Pass `replace: [...]` to atomically swap
  out an existing surface in the same operation.

- **`interactive_graph`** — draw or update a Mermaid diagram. Each
  diagram has a `name` you choose; same name = update in place,
  different name = add alongside. Supports flowcharts, sequence
  diagrams, classes, mindmaps, charts (bar/line/pie), gantt, sankey,
  timelines, ER, state machines. Diagrams are **ephemeral** — they
  appear, illustrate, and disappear on reload.

- **`request_new_block`** — author a novel interactive widget by
  calling out to the engineer LLM. Slow. Use **only** when no template
  fits and the requested thing isn't a diagram. Diagrams must go to
  `interactive_graph`; text/passages must go to `mount_template`.

- **`push_block_content`** — push a value into a topic that an existing
  block subscribes to. Drives live data (counters, list rows, text
  updates) into something already up — no remount.

- **`block_action`** — trigger a UI action on a mounted block:
  `highlight` (flash a glow), `focus` (move keyboard focus),
  `scroll_to` (scroll into view), `raise` (bring to front of stack).

- **`layout_blocks`** — reflow N blocks at once on the active grid.
  Pass `[{block_id, x, y, w, h}, ...]` plus `device_class` so the
  server validates against the right bounds (12×9 desktop, 8×9 tablet,
  4×9 phone — see `grid.md`). Use to fill empty space, place surfaces
  side-by-side, or honor an explicit resize request. No remount, PDFs
  stay on the same page.

- **`point_arrow`** — draw a labeled arrow from one block to another.
  Pass both ids empty to clear a previously-drawn arrow.

- **`speak`** — synthesize speech on the user's connected speakers.
  Use only when audio is genuinely better than text (short cues,
  alerts, hands-busy moments) and the user hasn't opted out.
