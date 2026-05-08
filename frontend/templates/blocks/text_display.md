---
keywords: introduction, summary, explanation, definition, overview, prose, paragraph, note
purpose: "Read-only prose surface the persona authors. Initial content rides on the mount via params.content; later updates arrive on the per-block content topic via push_block_content (accepts a raw string or an object with a content field)."
subscribes:
  - __CONTENT_TOPIC__
grid:
  x: 1
  y: 1
  w: 10
  h: 6
---

Use this template when the persona wants to put authored prose in front
of the user — an introduction, a definition, a one-paragraph summary,
an explanation. Distinct from `passage_reader`, which is the **user's**
input surface (paste/type their own text). `text_display` is the
**persona's** output surface.

Rendering: prose is rendered through `helpers.markdown` (GFM via the
host's `marked` instance), so tables, fenced code, blockquotes,
headings, lists, links, **bold**, *italic*, and `inline code` all work.
Write naturally — don't worry about HTML escaping; marked does it.

How it's filled:
- **At mount time** — pass `params: {content: "..."}` to
  `mount_template`. The string substitutes into the rendered JS so the
  block lands on the canvas already populated. This is the path that
  works in the user-facing single-iteration lane.
- **After mount** — push to `__CONTENT_TOPIC__` (per-block topic
  `text.<block_id>.content`) via `push_block_content` to replace the
  prose in place without remounting. Useful for follow-ups like
  "expand on the themes" — preserves scroll position and avoids
  flicker.

State reporting: emits `kind: "text"` with the first ~200 chars and
`extra.char_count`, so `read_media` reflects the current text.
