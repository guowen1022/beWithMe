---
keywords: input, launcher, start, choose, upload, paste, passage
purpose: Two-button starter block on an empty canvas. Lets the user pick a reading modality (upload PDF or paste passage). Each button mounts the corresponding reader template and unmounts the launcher.
grid:
  x: 50
  y: 35
  w: 60
  h: 20
backend:
  mount_template:
    method: POST
    path: /api/dynamic/mount-template
    auth: user
    content_type: application/json
    returns: json
---

The empty-canvas first paint mounts this block automatically (see
`DynamicSurface`). Once the user clicks a button, the launcher is replaced
in the same SSE batch by the chosen reader block.

The block calls the backend exclusively via `helpers.backend.mount_template`.
The frontend never hardcodes the `/api/dynamic/mount-template` URL — the
manifest above is the single source of truth.

`autosnapshot: false` because the launcher's content (two buttons) is not
something the persona needs to read about.
