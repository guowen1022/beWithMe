---
keywords: upload, file, attach, paper, document, pdf
purpose: Lets the user pick a PDF and uploads it to the backend.
publishes: __DOC_TOPIC__ — { id, title, pages }
---

Use this template when the user wants to provide a document to the system.
After the file is uploaded, the block publishes the document id on `__DOC_TOPIC__`
so a sibling block (e.g. `pdf_reader`) can fetch and render it.

Hits the existing backend endpoint `POST /api/documents/upload` (multipart;
`X-User-Id` header from `localStorage.bewithme_user_id`). The response shape is
`{ id, title, filename, text, pages }` — see services/knowledge/routers/documents.py.
