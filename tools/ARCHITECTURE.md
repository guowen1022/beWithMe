# tools — module architecture (owner reference)

> Scope reference for the **generic-tools** workstream. Root: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> North Star: [`../architecture-review/PRINCIPLES.md`](../architecture-review/PRINCIPLES.md) ·
> owner sim: [`../architecture-review/PROCESS.md`](../architecture-review/PROCESS.md) Step 4.

`tools/` holds the **general / public verbs** any persona may call — the ones that aren't specific
to one persona and aren't app-level. (Persona-specific tools live under `persona/<name>/tools/`;
app handlers under `persona/app_operator/tools/`; canvas verbs under `workshop/canvas/tools/`.)

## Territory
`browser_set`, `read_url`, `look_at_image`, `look_at_video`, `read_document`, `search_notes`,
`stream_emit`, `stream_query`, `stream_projection`, `write_to_inbox`, `read_captures`,
`read_world_knowledge`, `speak`, `web_view`, and `__init__.build_generic_specs(user_id)`.

## Protocol I consume
`infra.model.tools.ToolSpec`; `infra.silicon_brain_client.SiliconBrainClient` (user data over HTTP);
`infra.devices.delivery` (speak/web_view push); `infra.model.vision` / `infra.media` (look_at_*);
`infra.topology.upstream_url` (browser sidecar); `infra.contracts.*`.

## Protocol I provide
Each tool's `build_spec(user_id) -> ToolSpec`, plus `build_generic_specs(user_id)`. Consumed by the
persona manifests.

## Can I work alone?
Yes — add a general tool. The one coordination point: a new tool only reaches an LLM once the
consuming persona's `build_tools()` imports + lane-tags it (the persona owns its allowlist).

## Collisions (coordinate, don't parallelize)
- the persona manifests; the infra seams I depend on (`delivery`, `SiliconBrainClient`, `contracts`).

## Boundary rules I keep
1. A tool reaches a service **only over HTTP / an infra seam** — never `from persona.*` or
   `from services.*` internals, and never a `silicon_brain` ORM (this was F2; the rule the package
   docstring already states). Verified: `grep -rn "from persona\|from services\|from silicon_brain" tools/`
   shows only `infra.silicon_brain_client`.
2. Stay **general** — no persona-specific judgment here; if a verb only makes sense for one persona,
   it belongs under that persona.
