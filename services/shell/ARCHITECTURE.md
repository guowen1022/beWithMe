# services/shell — module architecture (owner reference)

> Scope reference for the **shell** workstream (the +0 sidecar). Root:
> [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) · North Star:
> [`../../architecture-review/PRINCIPLES.md`](../../architecture-review/PRINCIPLES.md) ·
> owner sim: [`../../architecture-review/PROCESS.md`](../../architecture-review/PROCESS.md) Step 4.

The shell is the **only public-facing process**: a reverse proxy + auth gate. It decides nothing
about content — it verifies identity, then forwards.

## Territory
`main.py` (the proxy + lifespan), `auth.py` (`X-User-Id` verify against the knowledge sidecar with a
60s TTL cache, CORS, and the `PUBLIC` path allowlist).

## Protocol I consume
`infra.topology` (`route_for_path`, `upstream_url`), `infra.auth`. (The knowledge sidecar's
`/api/auth/verify` for identity.)

## Protocol I provide
The single public HTTP surface. Routes `/api/<prefix>` to the owning sidecar per
`topology.PREFIX_TO_SERVICE`; forwards `X-User-Id` unchanged to the trusted network.

## Can I work alone?
Yes — proxy behavior, header handling, and the auth cache are mine.

## Collisions (coordinate, don't parallelize)
- `infra/topology.py` (the route table — adding a route is an infra/topology change);
  `services/shell/auth.py:PUBLIC` (the no-auth allowlist — every public path is a deliberate entry).

## Boundary rules I keep
1. I'm the **only** public door; every other sidecar trusts the forwarded `X-User-Id`.
2. No business logic — if a request needs a decision, it belongs in a persona behind me.
3. Every public path is explicitly in `auth.py:PUBLIC`; everything else requires `X-User-Id`.
