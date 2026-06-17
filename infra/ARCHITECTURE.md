# infra — module architecture (owner reference)

> Scope reference for the **infra** workstream. The root [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
> is the system map + dependency rules; [`../architecture-review/PRINCIPLES.md`](../architecture-review/PRINCIPLES.md)
> is the frozen North Star. This file records what the architecture-review **owner simulation**
> (Step 4 of [`../architecture-review/PROCESS.md`](../architecture-review/PROCESS.md)) found for
> this module — so one owner can work here without changing the shared **protocol**.

infra is the **leaf and the protocol provider**: the stateless foundation every other owner
builds on. Its job is to *offer* stable interfaces, not consume anyone's.

## Territory
`contracts/` (wire DTOs), `db.py` (Base/engine/async_session/get_db), `topology.py`
(SERVICE_OFFSETS + routing), `auth.py`, `config.py`, `silicon_brain_client.py`,
`model/` (llm facade, `tools.py` ToolSpec, `agent_loop.py`, deepseek/minimax/fake/vision
providers), `devices/` (registry, models=Device, canvas_layout, **delivery** SSE seam),
`perception/`, `rag/embedding.py`, `media/`, `render/`, `user_data.py`, `hlr.py`,
`observability.py`, `sandbox.py`, `templates.py`, `event_log*.py`, `tools/web_fetch.py`.

## Protocol I provide (every other owner depends on these)
- `contracts/*` DTOs — the wire types (ui, event, feed, inbox, devices, …).
- `silicon_brain_client.SiliconBrainClient` method signatures.
- `topology.SERVICE_OFFSETS` + the `/api/<prefix>` route table.
- `devices/delivery` — `enqueue_for_user/device`, `subscribe/unsubscribe`, `mounted_block_ids`.
- `devices/canvas_layout.CanvasLayout`, `devices/models.Device` (infra-owned, user-keyed device/canvas topology).
- `model/tools.ToolSpec` + the LLM facade (`model/llm.py`) + `model/agent_loop.run`.
- `db.Base/engine/get_db`, `auth.parse_user_id`, the `perception` API.

## Protocol I consume
**Nothing upward.** Only stdlib, third-party, and my own siblings. Enforced:
`grep -rnE "^(from|import) (silicon_brain|persona|services|workshop|agents)\." infra/` → **0**.

## Can I work alone?
Yes — I mostly *add* to the protocol, and additions are **backward-compatible** (a new optional
DTO field, a new client method, an appended SERVICE_OFFSET, a new tool). I refactor freely in
the **pure-internal** modules nobody imports across a boundary: `hlr.py`, `render/*`, `media/*`,
`rag/embedding.py`, `sandbox.py`, `templates.py`, `event_log*`, the `model/<provider>` impls.
The **protocol files** (`contracts/*`, `silicon_brain_client.py`, `topology.py`,
`devices/delivery.py`, `model/tools.py`, `model/llm.py`, `db.py`, `auth.py`) require care.

## Collisions (coordinate, don't parallelize)
Every owner eventually asks infra to *add* to the protocol — a DTO field, a `SiliconBrainClient`
method, a `SERVICE_OFFSET`, a delivery primitive. Those are coordination points **on infra**, but
they're cheap because they're additive. The only expensive change is a **removal / signature
change**, which ripples to all consumers — that's the one move that needs a coordinated PR.

## Boundary rules I keep
1. Import nothing upward — stay the leaf (the grep above must return 0).
2. Protocol changes are **add-only by default**; never remove/repurpose a DTO field, client
   method, offset, or seam signature without coordinating every consumer.
3. User-keyed tables may live here only when they're infra-domain topology (Device,
   CanvasLayout) — not the user's brain (that's silicon_brain).
