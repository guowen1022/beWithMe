# services/maestro — module architecture (owner reference)

> Scope reference for the **maestro** workstream (the +6 sidecar). Root:
> [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) · North Star:
> [`../../architecture-review/PRINCIPLES.md`](../../architecture-review/PRINCIPLES.md) ·
> owner sim: [`../../architecture-review/PROCESS.md`](../../architecture-review/PROCESS.md) Step 4.

Maestro is long-instance reasoning over the user's **event stream** + the **multi-persona landing
feed** (inter-source blend + saturation). It owns *when* to act and *what to surface*, not the
content (personas produce that).

## Territory
`main.py` (webhooks + scheduler), `gate.py` / `long.py` / `short.py` / `slice.py` (event gating +
long-instance decisions), `feed.py` / `blend.py` / `saturation.py` (feed assembly), `cache.py`
(in-memory, no DB).

## Protocol I consume
`infra.silicon_brain_client.SiliconBrainClient` (event stream + feed candidates),
`infra.topology.upstream_url` (persona callback over HTTP), `infra.contracts.{event,feed}`,
`infra.model.llm`.

## Protocol I provide
`/api/maestro` (event webhook from the agent layer) and `/api/feed` (the landing feed, read by the
frontend through the shell).

## Can I work alone?
Yes — gating, blend, saturation, and the scheduler cadence are mine. I touch user data only via the
client and personas only via `upstream_url` + HTTP.

## Collisions (coordinate, don't parallelize)
- `infra/contracts/{event,feed}.py`; `SiliconBrainClient` signatures; the feed-candidate store contract.

## Boundary rules I keep
1. Reach user data **only** through `SiliconBrainClient`; reach personas **only** via HTTP
   (`upstream_url`) — no persona/silicon_brain imports.
2. The landing feed is prepared **offline** (webhook + scheduler tick); the open path (`GET /api/feed`)
   is a pure cache read.
