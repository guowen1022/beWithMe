# 2026-06-17 — ownership simulation (Step 4, first run)

- **Reviewer:** Claude Opus 4.8 (1M context) + 4 owner-simulation agents
- **Git SHA:** post-F2 working tree
- **Scope:** First run of **Step 4 — Owner simulation**. Role-played one owner per module and asked: *can each advance their own workstream without changing the shared protocol?* Deep-simulated 4 owners (infra, silicon_brain, teacher, canvas); the rest assessed by synthesis.

This is the integration test for the architecture itself: if the principles hold, N owners build in parallel and the **protocol** is the only thing they coordinate on.

## Step 4 — Ownership & protocol simulation

| Owner | Territory | Independent? | Leak / collision |
|---|---|---|---|
| **infra** | `infra/` (the protocol provider) | ✓ | Pure leaf — zero upward imports. ~10 internal modules it changes freely; protocol files are **add-only safe** (add optional fields/methods, never remove). |
| **silicon_brain** | `silicon_brain/` + `services/knowledge/` | ✓* | Clean upward. Owns tables + knowledge routers + the inbox/feed DTO *content*. *But* its `preferences.py` imports the teacher persona → **F7**. |
| **teacher** | `persona/teacher/` | ✓ | Runtime-clean (no silicon_brain imports, no cross-persona imports). `prompts/*` silicon_brain imports are `TYPE_CHECKING`-guarded → allowed. Owns 95% of its tree. |
| **canvas** | `workshop/canvas/` | ✓ | Zero persona/services imports; consumes only infra seams (`delivery`, `canvas_layout`, `contracts/ui`, `ToolSpec`). Adding a *new* tool needs the consuming persona's manifest → coordination seam (by design). |
| app_operator | `persona/app_operator/` | ✓ | Tiny; consumes `ToolSpec` + `delivery` + `workshop.mount_template`; no private models. Clean (just rehomed in F2). |
| engineer | `agents/frontend_engineer/` | ✓ | Invoked via the teacher's `request_ui_block` tool; produces `BlockSource`; imports no persona internals. |
| maestro | `services/maestro/` | ✓ | Reaches user data only via `SiliconBrainClient` + `upstream_url`. Clean. |
| shell | `services/shell/` + topology | ✓ | Pure proxy + auth gate over `infra.topology`. Clean. |
| frontend | `frontend/` + `desktop/` | ✓ | Single API layer through the shell; mirrors `contracts` shapes in TS. |

**Protocol registry — off-limits to unilateral change** (provider → consumers):

| # | Protocol element | Provider | Consumers |
|---|---|---|---|
| P1 | `infra/contracts/*` DTOs (ui, event, feed, inbox, devices, …) | infra (dir) + data-owner (content) | everyone on the wire |
| P2 | one-direction dep graph (who imports whom) | — (Principles 1–2) | all |
| P3 | `SiliconBrainClient` method signatures | infra (file) + silicon_brain (semantics) | 20+ — tools, personas, maestro |
| P4 | knowledge sidecar endpoint paths (`services/knowledge/routers/*`) | silicon_brain | via P3 |
| P5 | `ToolSpec` shape + per-tool `build_spec(user_id)` + per-persona `build_tools()` | infra (`model/tools.py`) + each persona | canvas tools, generic tools, agent_loop |
| P6 | device/canvas delivery seam — `infra/devices/delivery` (enqueue/subscribe/mounted_block_ids) | infra | canvas tools, persona triggers, the `/api/dynamic` router |
| P7 | `CanvasLayout` ORM (`infra/devices/canvas_layout.py`) | infra | canvas tools |
| P8 | `infra/topology.SERVICE_OFFSETS` + route table | infra | all sidecars |
| P9 | `infra/db.py` `Base/engine/get_db` | infra | all ORM domains |
| P10 | `infra/model/llm.py` facade + `infra/model/agent_loop.run` | infra | personas, maestro, tools |
| P11 | persona `addressee` dispatch (`/api/ask`) | shell/persona | frontend |

The registry is **stable and add-only-safe** today: a DTO field, a client method, a sidecar offset, or a tool can all be *added* without breaking existing owners. Only *removals/signature changes* ripple — those are the coordination PRs.

**Conflict map — seams that need coordination, not parallelism:**

1. **Persona manifest ↔ tools** (the main one): a new canvas/generic tool can't self-register — the consuming persona's `build_tools()` must import + lane-tag it (`persona/teacher/tools/manifest.py`). Canvas owner + persona owner coordinate. *By design* (a persona owns its allowlist), but it's the seam two owners hit most.
2. **`infra/silicon_brain_client.py`**: silicon_brain owner adds methods; infra owns the file; signature *changes* ripple to 20+ consumers.
3. **`infra/contracts/ui.py`**: canvas + personas + frontend all depend — shape changes need all three.
4. **`infra/topology.SERVICE_OFFSETS`**: append-only; adding a sidecar.

**Verdict:** **9/9 owners can work in parallel on their internals without touching the protocol** — the architecture supports the goal. Two caveats:
- **F7 (a real leak)** entangles the silicon_brain owner and the teacher owner at the preferences endpoint, and blocks adding a *second* persona owner cleanly.
- The canvas→persona tool-registration seam is healthy coordination, not a defect — documented above so owners expect it.

## New findings

### F7 — Ownership inversion: the knowledge sidecar imports the teacher persona
- **Dimension:** D1 (decoupling)
- **Severity:** medium-high
- **Evidence:** `services/knowledge/routers/preferences.py:8` — `from persona.teacher.preferences import distill_preferences, get_or_create_preferences`. The knowledge sidecar (the silicon_brain owner's HTTP face, meant to be a neutral user-data provider) depends **up** on the teacher persona. Flagged independently by both the silicon_brain and teacher owner simulations.
- **Why it matters:** breaks the one-direction graph (`services/knowledge → persona/teacher`); hardwires a generic data endpoint to *one* persona. A second persona (helper/engineer) can't be added without either duplicating this router or also depending on teacher code — i.e. it **blocks parallel persona ownership**, the exact thing Step 4 checks for. (Note: the F2/M3 voice-pref work correctly used `talk_preference.py` — the *non-inverted* UserPreferences face — which is why it stayed clean.)
- **Recommended action:** move preference distillation behind a **teacher-owned** endpoint on the persona sidecar that the knowledge router delegates to over HTTP, or relocate the `/api/preferences` distill endpoint to the persona sidecar entirely. Then `/api/preferences` (teacher's distilled `TeacherPreferenceModel` view) is owned by the teacher, and the knowledge sidecar stays persona-agnostic.
- **Status:** ✅ resolved 2026-06-17 — moved `services/knowledge/routers/preferences.py` → `services/persona/routers/preferences.py` (the teacher's HTTP face, where importing `persona.teacher` is legitimate); added `"preferences": "persona"` to `topology.PREFIX_TO_SERVICE` so the shell routes `/api/preferences*` to the persona sidecar; removed the dead `SiliconBrainClient.get_user_preferences` (0 callers, would 404 after the move). **Also** decoupled the *second* knowledge→teacher edge found during the fix — `services/knowledge/main.py` imported `persona.teacher.models` for metadata registration; replaced with `infra.user_data.load_domains()` (dynamic imports inside infra), so the knowledge sidecar now names **zero** persona code. Verified: `grep persona services/knowledge/` clean, both sidecars boot, routing correct (`preferences`→persona, `talk-preference`/`voice-preference` stay→knowledge), e2e green (talk_preference_prompt + ask_flow). The knowledge sidecar is now truly persona-agnostic → a 2nd persona can be added without touching it.

### Note (not a finding) — `TYPE_CHECKING` imports are compliant
`persona/teacher/prompts/{answer,voice_answer,voice_brief}.py` import `silicon_brain.models.document.DocumentChunk`, but under `if TYPE_CHECKING:` — allowed by invariant 3. The enforced runtime grep (`^(from|import) silicon_brain` in `persona/`) is clean. Recorded so a future round doesn't re-flag them.

## Notes

- First Step-4 run — establishes the protocol registry as the baseline yardstick for "did the protocol change?" between rounds. Next round: diff the registry; an *unchanged* registry + all-owners-independent is the green signal that parallel work is safe.
- The owner simulation found 1 real leak (F7) that the dimension scoring (Step 2) and map reconciliation (Step 1) had *not* surfaced — the per-owner adversarial lens earns its place.
