---
keywords: upload, file, attach, paper, document, pdf
purpose: Lets the user pick a PDF and uploads it to the backend.
publishes:
  - __DOC_TOPIC__
grid:
  x: 3
  y: 4
  w: 6
  h: 1
backend:
  upload:
    method: POST
    path: /api/documents/upload
    auth: user
    content_type: multipart/form-data
    returns: json
  mount_template:
    method: POST
    path: /api/dynamic/mount-template
    auth: user
    content_type: application/json
    returns: json
---

Use this template when the user wants to provide a document to the system.
After the file is uploaded, the block publishes the document id on `__DOC_TOPIC__`
so a sibling block (e.g. `pdf_reader`) can fetch and render it.

The block calls the backend via `helpers.backend.upload(formData)`. The
manifest declares `POST /api/documents/upload` with multipart body and
auto-injected user auth. The response shape is
`{ id, title, filename, text, pages }` — see services/knowledge/routers/documents.py.

Reports `{kind: "upload", content: "...", extra: {document_id}}` via
`helpers.reportState` so the persona's `read_media` can see what the user
just uploaded.
