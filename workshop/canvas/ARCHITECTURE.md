# workshop/canvas — module architecture (owner reference)

> Scope reference for the **canvas** workstream (the canvas mutation verbs). Root map:
> [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md); North Star:
> [`../../architecture-review/PRINCIPLES.md`](../../architecture-review/PRINCIPLES.md); this records the
> **owner simulation** (Step 4 of [`../../architecture-review/PROCESS.md`](../../architecture-review/PROCESS.md)).

The canvas module is the set of **block verbs** personas call to put things on the user's screen.
It is **stateless** — all durable state lives in infra (delivery / perception / canvas_layout) or
in a persona (research_state).

## Territory
`tools/*` — `mount_template`, `edit_note`, `block_action`, `push_block_content`, `point_arrow`,
`layout_blocks`, `interactive_graph`, `request_ui_block`, `read_media`, `list_media`; the support
internals `_note_cache`, `_note_index`, `_note_chunker`, `_slug`, `_template_registry`; and
`skills/*` (grid model, lifecycle, layering docs).

## Protocol I consume
- `infra.contracts.ui` — `UIUpdate`, `BlockMessage`, `BlockAction`, `BlockSource`, `BlockError`, `GridPos`.
- `infra.devices.delivery` — `enqueue_for_user/device`, `mounted_block_ids` (push to the device).
- `infra.devices.canvas_layout.CanvasLayout` — the per-device mount table (read/write via `infra.db`).
- `infra.model.tools.ToolSpec`; `infra.render/*`, `infra.sandbox`, `infra.templates`, `infra.perception`.
- `agents.frontend_engineer.workspace` (for `request_ui_block` → engineer-built blocks).

## Protocol I provide
`build_canvas_specs(user_id)` + each tool's `build_spec(user_id) -> ToolSpec`. Consumers: the
persona manifests — `persona/teacher/tools/manifest.py` and `persona/app_operator` (show_mirror).

## Can I work alone?
Yes — for tool logic, mount-grid defaults, and rendering/caching internals. The one structural
coordination point: a **new** canvas tool can't self-register — the consuming persona's
`build_tools()` must import it and tag its lane. (By design: a persona owns its tool allowlist.)

## Collisions (coordinate, don't parallelize)
- the **persona manifests** (`persona/teacher/tools/manifest.py`, `persona/app_operator`) — tool registration.
- `infra/contracts/ui.py` (shared with personas + frontend), `infra/devices/delivery.py`,
  `infra/devices/canvas_layout.py`.
- `frontend/templates/blocks/*` — the frontend owns the template *source*; I only `load_template()` + mount it.

## Boundary rules I keep
1. **Zero persona/services imports:** `grep -rn "from persona\|from services" workshop/canvas/tools/`
   → **0** (this was F2 — the SSE seam moved to `infra.devices.delivery`).
2. Stay stateless — never hold canvas state in module globals; it lives in infra or a persona.
