---
keywords: mirror, events, event stream, history, activity, log, reflection, what happened, timeline
purpose: Read-only view of the user's event stream — every recorded event grouped by source. The dark, on-canvas relocation of the old /mirror debug page.
publishes: []
subscribes: []
grid:
  x: 0
  y: 0
  w: 5
  h: 7
backend:
  query_stream:
    method: POST
    path: /api/event-stream/query
    auth: user
    content_type: application/json
    returns: json
---

Mounted by the app_operator `show_mirror` action. Renders the user's durable
event stream (`POST /api/event-stream/query` → `list[EventDTO]`) as a dark,
scrollable panel: events grouped by `source` (user, agent, maestro_long,
maestro_short, signal, system, capture), each row showing the timestamp, a
colour-coded source badge, the event `kind`, and the JSON `body`.

Read-only developer/debug surface — the canvas successor to the `/mirror`
route, gated by `BEWITHME_DEBUG` on the mount side. A `refresh` control
re-queries the stream. Reports a `{kind: "mirror"}` state via
`helpers.reportState` so the persona can see what's on screen.
