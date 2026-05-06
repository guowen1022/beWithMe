---
keywords: pdf, read, view, show, render, document, paper
purpose: Renders every page of a PDF inside the block, with selectable text. Pages render lazily as they scroll into view so the document feels instant on long PDFs.
subscribes:
  - __DOC_TOPIC__
publishes:
  - __SELECTION_TOPIC__
grid:
  x: 0
  y: 0
  w: 12
  h: 9
---

Use this template when the user wants to read or view a PDF on the canvas.
Pair with the `upload_file` template (or any source that publishes a
document id on the same topic).

Loads the raw PDF bytes from the existing `GET /api/documents/{id}/pdf`
endpoint, then builds a placeholder `<div>` per page sized to the real
viewport so the scroll height is correct up-front. An IntersectionObserver
renders each page's `<canvas>` + transparent text layer (via
`window.pdfjsLib`) only when the placeholder approaches the viewport,
which is what makes long PDFs feel instant — page 1 paints immediately
and the rest stream in as the user scrolls. Mouse selections inside the
rendered text layer produce real DOM selections; the block listens on
`mouseup` and republishes the selected text on `__SELECTION_TOPIC__`.
