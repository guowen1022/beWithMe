---
name: principles
always: true
when: The read-first / collect-first / then-write loop. Universal — every operation inherits it.
---

# Principles — read first, collect first, then write

These are the rules every operation in this agent inherits. Read them
before you do anything else on a turn.

## The order

1. **READ.** The workspace dump (README, TOPICS, every existing block,
   CAUTIOUS) is already in this prompt. Read it. Find blocks the user
   already has. If the request is satisfied by something on the canvas,
   say so in your plan and do not write code.

2. **COLLECT.** The template reference (`frontend/templates/blocks/*`)
   is already in this prompt. Match the user's command against each
   template's `keywords:` line. Pick the best fit. If two templates
   compose (e.g. upload → reader), pick both and agree on a shared bus
   topic.

3. **WRITE.** Emit only what changed. Prefer copying a template chunk
   over handwriting JS. Handwrite only when steps 1 and 2 give you
   nothing usable.

## Why the order is non-negotiable

Every byte of LLM-generated JS that lands in a user's workspace is a
chance to corrupt layout, fonts, or behavior the templates already
get right. The user pays for retries — both in latency and in the
trust they extend to the canvas. The cheapest correct answer is the
one where we wrote the least new code.

## Concrete examples

- **"Upload a paper and read it."** Step 1: no upload block yet. Step 2:
  `upload_file` and `pdf_reader` templates match (`upload, file, paper,
  document, pdf, read`). Step 3: copy both, share `uploaded_doc`. Done.

- **"Move the upload bar lower."** Step 1: an upload block exists. Step
  2: irrelevant — no template needs to match for a move. Step 3: emit
  only the grid change (today: full re-emit; planned: `### grid`
  partial edit). Do not touch any other field.

- **"Hide the reader for now."** Step 1: reader exists. Step 2:
  irrelevant. Step 3: prefer `### deleted` (reversible from git) over
  rewriting the block's style to `display: none`. When the planned
  `### hidden` verb lands, prefer that.

## What "minimum code" means in practice

- Re-emit a file only if it changed. Never paste back an unchanged
  block.
- Adapt template placeholders (`__BLOCK_ID__`, `__GRID_X__`,
  `__DOC_TOPIC__`) — don't rewrite the surrounding code "to be cleaner".
- If two ways to satisfy the request exist and one writes less code,
  pick the smaller one. Ties go to the one that touches fewer files.

## When to write a CAUTION

Only when the turn taught you something durable — a specific user
preference, a template pitfall, a topic-naming gotcha. Not for generic
advice. CAUTIOUS.md is read every turn; bloat there poisons future
prompts.

## Reporting block state (perception)

The teacher persona reads what the user is currently receiving by calling
`read_media`, which returns each mounted block's latest self-reported
state. Every block participates — there is no opt-out.

There are two tiers:

### Tier 1 — rich (preferred)

Your block calls `helpers.reportState({...})` whenever something the
persona would care about changes. Use a concrete `kind` and a one-line
human-readable `content` summary; put structured data in `extra`.

```js
// inside block.run(root, bus, cleanup, helpers):
helpers.reportState({
  kind: "pdf",
  content: `page ${page} of ${total}: ${visibleSnippet}`,
  extra: {
    page,
    total,
    scroll_pct: scrollY / scrollHeight,
    visible_chunks: visibleChunkIds,
  },
});
```

Call it on *real* state changes — page turn, value tick, item selected —
not on every animation frame. The reporter debounces (~200 ms) so a
burst of changes coalesces, but emitting every frame still wastes CPU.

### Tier 2 — automatic snapshot (fallback)

If your block doesn't call `helpers.reportState`, `Block.tsx` watches
the block's DOM for mutations and reports
`{kind: "snapshot", content: root.innerText}` for you. Cheap, universal,
honest. You don't need to do anything to get this.

Prefer tier 1 only when you have structured data the persona would
benefit from (page numbers, IDs, counters). For "block that displays
text and changes when its bus topic ticks", the snapshot is fine.

### `focus` is filled in for you

The frontend tracks which block has the user's attention (mouseover or
keyboard focus) and stamps every state report with `focus: "active" |
"visible" | "background"`. Never set it yourself unless you have very
specific reason to override; let the focus tracker decide.

### Don't

- Don't report from inside an animation frame loop. Use real state
  events (input change, fetch settled, message received).
- Don't put gigabytes in `extra`. The cache caps `content` at 1000
  chars; keep `extra` similarly compact (IDs, counts, summaries — not
  raw file contents).
- Don't try to opt out of reporting. If a block is mounted on a canvas,
  it's by definition something the user can receive. The persona must
  be able to read it.
