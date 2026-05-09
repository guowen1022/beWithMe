---
keywords: web view browser embed live page replay url visit open
purpose: "Position the desktop's real Chromium pane (Electron BrowserView) inside the canvas grid. The block itself renders only a frame + chrome; the actual webpage runs in a top-level Chromium context that the persona drives via the web_view tool's HTTP shim. Use for live URLs that fail in iframes — anti-embed sites, session-bound SPAs, video/canvas players."
subscribes: []
publishes: []
grid:
  x: 1
  y: 1
  w: 10
  h: 7
---

The web_view block is a *positional placeholder*. Its DOM is just a
header strip + a transparent body. The actual page contents are rendered
by the Electron BrowserView floating on top, sized to the block's body
rect via `window.beWithMeBridge.browser.setBounds`.

Why a separate Chromium pane (and not an iframe):
- The page loads first-party — its own cookie jar, real `Referer`, no
  `window.top` self-checks, no storage partitioning. SPAs that go blank
  inside `request_new_block` (anti-embed headers, missing session
  cookies) work here.
- The persona drives navigation/perception via the `web_view` tool, which
  POSTs to a token-authed HTTP shim in `desktop/src/web_view_shim.ts`.
  The block does *not* navigate — it only handles position and lifecycle.

When this block isn't running inside Electron (e.g. someone opens
`localhost:3000` in a browser), the bridge is unavailable and the block
renders an "open in desktop to view this page" placeholder. The persona's
`web_view` tool also returns `{"error": "desktop_not_running"}` in that
case, so the persona can speak the limitation back to the user.

Lifecycle:
- On mount: read `getBoundingClientRect()` of the body, call
  `bridge.browser.setBounds(rect)`. Set up a `ResizeObserver` on the body
  + `window.resize` listener so dragging/resizing the block keeps the
  pane aligned.
- On unmount (user closes the block via grid controls, or persona calls
  `web_view(action='close')`): call `bridge.browser.hide()`. The page
  contents stay loaded in memory but become invisible.
- State reporting: emits `kind: "web_view"` with the BrowserView's
  current URL + title (read via `bridge.browser.getCurrentUrl()` and
  `onUrlChange`). The persona's `read_media` sees this alongside other
  blocks.

This template takes no params. The persona uses `web_view(open, url=...)`
to mount + navigate as one logical action.
