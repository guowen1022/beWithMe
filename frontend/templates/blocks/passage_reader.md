---
keywords: paste, passage, text, snippet, excerpt, type
purpose: Lets the user paste or type a passage of text. The passage stays in the block; the persona reads it via read_media. Selection inside the passage is published on a topic so other blocks can react.
publishes:
  - __SELECTION_TOPIC__
grid:
  x: 1
  y: 1
  w: 10
  h: 8
---

Use this template when the user wants to discuss text they have on hand
(rather than uploading a PDF or opening a URL). The user pastes or types
into the textarea; nothing leaves the block until the persona asks.

The block reports its current passage state via `helpers.reportState`
(debounced as the user types) so `read_media` returns:
- `kind: "passage"`
- `content`: first ~200 chars of the passage
- `extra.char_count`: total length
- `extra.selection`: any text the user has highlighted right now

No backend frontmatter — this block has no backend dependency.
