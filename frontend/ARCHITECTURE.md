# frontend (+ desktop) — module architecture (owner reference)

> Scope reference for the **frontend** workstream (Next.js web app + the Electron desktop shell).
> Root: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) · North Star:
> [`../architecture-review/PRINCIPLES.md`](../architecture-review/PRINCIPLES.md) ·
> owner sim: [`../architecture-review/PROCESS.md`](../architecture-review/PROCESS.md) Step 4.
> See also [`AGENTS.md`](./AGENTS.md) for the custom Next.js notes (read before touching Next-specific code).

The user's surface: a Next.js app rendered on a device, optionally wrapped by an Electron shell
(`../desktop/`). It is the *audience* side — it never decides; it renders what personas deliver.

## Territory
`frontend/app/*`, `frontend/components/*`, `frontend/lib/*` (incl. the single API layer
`lib/api.ts`, the SSE/block bus, `eouGate`, `blockState`); and `../desktop/src/*` (`main.ts`,
`preload-*`, `web_view_shim.ts`).

## Protocol I consume
The **shell** HTTP surface — *all* backend calls go through one base URL (`/api` → shell); the SSE
dynamic stream (`GET /api/dynamic/stream`) carrying `UIUpdate` / `BlockMessage` / `BlockError`; the
`infra/contracts` shapes (mirrored in TS).

## Protocol I provide
The rendered UI; the device headers (`X-Device-Id/Class/Capabilities`) that register a device on SSE
connect; block-state reports to `POST /api/dynamic/state`.

## Can I work alone?
Yes — components, lib, and the desktop shell are mine. **Coverage strategy (owner decision, F4/F5):**
the frontend and desktop are covered by **e2e**, not component/unit tests — frontend unit tests
"cannot find anything useful." P2 (self-contained = testable) is satisfied here at the **e2e layer**:
the backend e2e suite exercises the API + SSE contracts, and browser-level e2e is the vehicle for UI
behavior.

## Collisions (coordinate, don't parallelize)
- the TS mirrors of `infra/contracts/*` (UI/event shapes); the `/api/dynamic/*` event types; the
  shell route surface.

## Boundary rules I keep
1. Talk to the backend **only through the shell** (one base URL via `lib/api.ts`) — never a sidecar
   port directly.
2. The `web_view` block renders in a separate Chromium top-level context (desktop `main.ts`), not in
   the React origin — the deliberate out-of-frame exception (root ARCHITECTURE.md §5.4).
