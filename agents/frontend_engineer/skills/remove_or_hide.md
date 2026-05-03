---
name: remove_or_hide
keywords: remove, delete, drop, hide, kill, clear, close, dismiss, get rid, take away, no longer
when: Drop or hide an existing block.
---

# Remove or hide

Operation: drop a block from the canvas, or hide it without losing the
source. Like repositioning, **do not rewrite the body** to make a block
disappear.

## Order of work (inherits `principles.md`)

1. **Read** the workspace dump. Confirm the block exists; confirm it is
   the one the user means (match by id, by title in the `.md`, or by
   the role described in their command).
2. **Collect** — irrelevant for this operation.
3. **Write** the smallest verb that satisfies the request.

## Today's protocol — `### deleted` is the only first-class verb

Add a `### deleted` section listing the block ids:

```
### deleted
- pdf-reader-old
- upload-stale
```

The runtime drops `<id>.js` and `<id>.md`, de-mounts the block, and
regenerates `TOPICS.md`. Reversible from git history.

## Hide without deleting (today)

There is no first-class hide verb yet. When the user says "hide for
now, I might bring it back," prefer **delete** over rewriting the
block's `style` to `display: none`:

- Delete is reversible (`git show HEAD~1 -- blocks/<id>.js` recovers
  it; the engineer can re-add it on a future turn).
- Rewriting the style touches the template-faithful body — exactly
  what this operation is meant to avoid.

Only rewrite the style when the user explicitly says "keep it on
disk; I'm coming back to it in a few seconds." Even then, change only
`style.display` (or wrap it in a `hidden` flag the run() reads). Do
not touch any other field.

## Planned hide verb — `### hidden` / `### shown`

When the protocol gains `### hidden`, the runtime will skip render for
listed ids while keeping their files. Reverts via `### shown`. At that
point, hide stops being a delete-or-rewrite tradeoff. The skill's
preferences flip: `### hidden` becomes the default; delete is reserved
for "I never want this block again."

## Disambiguation

- "**Remove the upload**": delete. The user wants it gone.
- "**Hide the upload while I'm reading**": prefer delete today (planned:
  `### hidden`). They can re-add later.
- "**Move the upload off-screen**": that's a reposition, not a hide.
  Use `repositioning.md`.
- "**Replace the upload with a quiz**": that's two operations: delete
  the upload, then new block for the quiz. State both in your plan.

## What this operation never does

- Rewrite `<id>.js` to "neutralize" the block (e.g. emptying `run()`,
  blanking `content`). That's a body edit pretending to be a hide.
- Delete a block to fix a different block's bug. If block B isn't
  working, fix B; don't kill A in the hope of clearing some side effect.
- Delete a topic-publishing block while another block still subscribes
  to that topic. Check `TOPICS.md` first — orphan subscribers waste
  cycles and confuse the user.
