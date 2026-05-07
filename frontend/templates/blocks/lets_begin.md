---
keywords: begin start lets let's go ready start-here begin-session
purpose: First-paint hero card with a single CTA. Click "Let's Begin" to start the session — mounts the ambient_mic block in place.
grid:
  x: 4
  y: 3
  w: 4
  h: 3
backend:
  mount_template:
    method: POST
    path: /api/dynamic/mount-template
    auth: user
    content_type: application/json
    returns: json
---

A simple welcome card with one button. Mounts the `ambient_mic` block
in the same SSE batch on click (replaces itself), so the canvas
transitions cleanly from "ready when you are" to a live mic.

Calls the backend exclusively through `helpers.backend.mount_template`.
The frontend never hardcodes the `/api/dynamic/mount-template` URL —
the manifest above is the single source of truth.

`autosnapshot: false` because the persona doesn't need to read "BEGIN"
back from the canvas.
