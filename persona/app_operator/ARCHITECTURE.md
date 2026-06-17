# persona/app_operator — module architecture (owner reference)

> Scope reference for the **app_operator** workstream. Root: [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) ·
> North Star: [`../../architecture-review/PRINCIPLES.md`](../../architecture-review/PRINCIPLES.md) ·
> owner sim: [`../../architecture-review/PROCESS.md`](../../architecture-review/PROCESS.md) Step 4.

A minimal sibling persona that performs **app-level** actions (the UI *shell*, not a single block):
`switch_user`, `go_home`, `show_mirror`. No private models, no brain reads — deliberately tiny.

## Territory
`agent.py` (respond loop, `max_iterations=2`), `tools/manifest.py`, `tools/app_action.py`
(the 3 app-action specs).

## Protocol I consume
`infra.model.tools.ToolSpec` + `infra.model.agent_loop` (the shared tool loop), `infra.contracts.ui.AppAction`,
`infra.devices.delivery` (emit the `AppAction` SSE — *app handling directly*, not a persona router),
`workshop.canvas.tools.mount_template` (for `show_mirror`).

## Protocol I provide
`build_tools(user_id)` consumed by the persona sidecar's ask router via the `addressee="app_operator"` path.

## Can I work alone?
Yes, trivially — add an app-level verb, bind it in `tools/manifest.py`. No DB, no brain, no other persona.

## Collisions (coordinate, don't parallelize)
- `workshop/canvas/tools/mount_template` (canvas owner), `infra/contracts/ui.py:AppAction`,
  the persona sidecar's `addressee` dispatch.

## Boundary rules I keep
1. No `silicon_brain` imports; no imports of another persona's internals (I drive the generic
   `infra.model.agent_loop`, not the teacher's loop).
2. App actions emit through the `infra.devices.delivery` seam (F2) — never a service router internal.
