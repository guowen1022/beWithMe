# Template-first block authoring

Before writing custom block JS, look at `frontend/templates/blocks/`.
Every template is a `<name>.js` (parens-wrapped block source with placeholders)
plus a `<name>.md` (frontmatter describing keywords, purpose, and the bus
topics the block publishes/subscribes to).

The matcher in `agents/frontend_engineer/build.py` already does keyword
scoring against each template's `keywords:` line. Multiple templates can
match a single request — when they do, compose them by wiring shared bus
topics so one block's output feeds another's input.

## Composing templates

When two templates participate in the same workflow, they must agree on a
topic name. The agent assigns these from a small palette per recognized
intent (e.g., for "upload a PDF and read it" the canonical pair is
`upload_file` → `pdf_reader` sharing `__DOC_TOPIC__ = "uploaded_doc"` so the
upload's published id is delivered to the reader's subscriber).

## When to skip templates

Fall back to handwriting block JS only if **no template at all matches** —
e.g., a one-off widget the user explicitly described in low-level UI terms.
Even then, prefer to commit the handwritten block as a new template if the
shape is reusable.

## Current templates

- **upload_file** — file picker → POST `/api/documents/upload` → publishes
  `{id, title, pages}` on the wired topic. Keywords:
  upload, file, attach, paper, document, pdf.
- **pdf_reader** — subscribes to a doc topic; on payload arrival, fetches
  `/api/documents/{id}/pdf` and renders page 1 with `window.pdfjsLib`;
  republishes mouse-selected text on a `_selection` sibling topic.
  Keywords: pdf, read, view, show, render, document, paper.
