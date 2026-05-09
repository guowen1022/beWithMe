---
keywords: url card link reference read article webpage page
purpose: "Compact perception card for a URL the persona just read silently via read_url. Shows just enough (title + 1-line excerpt + host) to confirm the page was processed without dumping its full content back at the user. Persists on canvas so subsequent persona turns can refer to it via read_media."
subscribes: []
publishes: []
grid:
  x: 1
  y: 1
  w: 6
  h: 2
---

The url_card block is the persona's persistent memory of a URL it
silently read. It exists for two reasons:

1. **User feedback.** The user shared a URL; without any UI response,
   they don't know whether the persona processed it. The card is a
   tasteful "yes, I read this" confirmation — far less invasive than
   a full text_display dump.
2. **Persona perception.** The result of `read_url` would otherwise
   only live in the calling turn's tool-result context. By mounting it
   as a canvas block, the title + excerpt + URL show up in
   `read_media` and CURRENTLY ON CANVAS on every subsequent turn —
   so Lane A reflects, follow-up questions, and any later persona
   call has the URL's content available.

Layout: a single horizontal chip — favicon-style icon, the page title
(bold, single line, truncated), a one-line excerpt, and the URL host.
No body, no scroll, no controls. Designed to occupy a 6×2 cell at the
top of the canvas without competing with the user's primary surface.

State reporting: emits `kind: 'url_card'` with `content: '<title> ·
<host>'` and `extra: {url, title, excerpt}`. The excerpt is the first
~300 chars of the page text, plenty for the persona to ground answers
about general intent without re-reading.

The card is replaceable: subsequent `read_url` calls overwrite the
existing `url-card` block by id. Only the most recent URL card lives
on canvas at a time.
