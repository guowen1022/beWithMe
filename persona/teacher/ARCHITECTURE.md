# persona/teacher — module architecture (owner reference)

> Scope reference for the **teacher** workstream. Root map:
> [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md); North Star:
> [`../../architecture-review/PRINCIPLES.md`](../../architecture-review/PRINCIPLES.md); this records the
> **owner simulation** (Step 4 of [`../../architecture-review/PROCESS.md`](../../architecture-review/PROCESS.md)).

The teacher is the LLM persona that decides what to do for the user. It owns its voice,
judgment, tools, and its own data — and reaches the user's brain only over HTTP.

## Territory
`agent.py`, `contexts/*` (answer/research/reflect), `triggers.py`, `writer.py`, `engagement.py`;
`tools/manifest.py` + `tools/read_concept_mastery.py` + the research-lane tools; `research_state.py`;
`models/*` (Interaction, LearningGoal, Recommendation, LearningSession, TeacherPreferenceModel,
ConceptNode/Edge); `knowledge/` (concept graph); `preferences/` (distillation); `prompts/`,
`skills/`, `recommender/`, `feed/`, `goals/`, `session/`, `brain_builder/`.

## Protocol I consume
- `infra.model.tools.ToolSpec` + `infra.model.agent_loop.run` (the tool loop).
- `infra.silicon_brain_client.SiliconBrainClient` (the *only* way I read the user's brain).
- `infra.contracts.*` (event, feed, output_routing).
- `infra.devices.delivery.enqueue_for_user` (push to the user's canvas).
- the generic `tools/` package + `workshop.canvas.tools` (canvas verbs) — via their `build_spec(user_id)`.

## Protocol I provide
The **persona sidecar** routers (`services/persona/routers/*`) import my models, schemas, context
builders, and tool loop. After F7, the persona sidecar's `/api/preferences` is powered by my
`preferences/` distillation functions (now in-layer — persona sidecar hosting teacher logic, fine).

## Can I work alone?
Yes — I own ~95% of my tree. I change prompts, skills, models, knowledge graph, research lane, and
context assembly freely. Constraints: respect the `ToolSpec` / `build_spec(user_id)` contract;
adding a teacher-only tool is **my own manifest edit**; producing a new field on an outbound
contract DTO needs infra.

## Collisions (coordinate, don't parallelize)
- `infra/contracts/*` — if I want new fields on event/feed payloads I produce.
- `workshop/canvas/tools/*` — shared with the **canvas owner**; adding a canvas tool means I must
  import + lane-tag it in `tools/manifest.py`.
- the generic `tools/` package — its `build_spec(user_id)` contract.

## Boundary rules I keep
1. **Zero runtime `silicon_brain` imports:** `grep -rnE "^(from|import) silicon_brain" persona/teacher/`
   → **0**. `TYPE_CHECKING`-guarded type hints (e.g. in `prompts/*`) are allowed.
2. No imports of another persona's internals (`persona/app_operator`, `agents/frontend_engineer`).
3. I may query **my own** `models/*` tables directly via `infra.db`; everything else in the brain
   goes through `SiliconBrainClient`.
