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
