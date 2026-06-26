# 2026-06-26 — full sweep (authz + end-session)

- **Reviewer:** Claude Opus 4.8 (1M context) + 7 owner/scoring sub-agents
- **Git SHA:** `d2ac2cc`
- **Scope:** Full sweep. Work under review = the two un-reviewed commits since the last round
  (2026-06-17 ownership-sim): `fa274a4` (teacher end-session, two-stage) + `d2ac2cc`
  (per-persona tool authorization, domain grants). Diff range `d807495..d2ac2cc`. Every
  architectural part re-scored (Step 2), not just the touched ones.
- **Mode:** **Report-only** — no code or reviewed-doc was changed. Every issue is recorded as an
  open finding for a later round. The only artifacts are this entry + the `LEDGER.md` index line.

## Step 0 — Mechanical evidence (cited, not the verdict)

| Check | Result |
|---|---|
| `grep -rnE "^(from\|import) (app\|silicon_brain\|persona\|services)\." infra/` | **0 hits** (leaf clean; F1 stays fixed) |
| `grep -rnE "^(from\|import) (app\|persona\|services)\." silicon_brain/` | **0 hits** |
| `grep -rnE "^(from\|import) silicon_brain" persona/` | **0 runtime hits** (3 `TYPE_CHECKING`-guarded in `prompts/*` — allowed) |
| `pytest tests/unit/test_user_data_map.py tests/unit/test_tool_authz.py -q` | **14 passed** |
| import smoke (`persona.teacher`, `services.persona.main`, `services.knowledge.main`) | **OK** |
| `pytest tests/e2e/test_manifest_refactor.py tests/e2e/test_e2e.py -q` | **30 passed, 22 skipped** |

**e2e limitation (no silent gap):** the 22 skips are not failures — the e2e fixture boots
sidecars via `VENV_PYTHON = REPO_ROOT/.venv/bin/python` (`tests/e2e/conftest.py:154`), and this
**git worktree has no local `.venv`** (it lives in the main checkout), so the sidecar-booting
tests skip at conftest. The 30 that ran are the no-boot boundary assertions. The full 167-test
e2e suite was recorded green at this exact SHA by the authz proposal
(`proposals/2026-06-17-tool-authorization.md`: "30 manifest + 216 unit + 167 e2e green").

## Step 1 — Map reconciliation (ARCHITECTURE.md ↔ code)

| # | Discrepancy (doc ↔ code) | Truth | Resolution |
|---|---|---|---|
| 1 | §4.4 authz description ↔ `infra/model/authz.py`, `tools.py` `ToolDomain`+`ToolSpec.domain`, per-persona `grants.py`, two enforcement points (assembly `manifest.py:547` + dispatch `agent_loop.py:69-76`) | Map matches code | no change |
| 2 | §4.5 two-stage dispatch ↔ `ask.py:208-294` (Stage 1) + `_ask_session.py:35-56` (Stage 2, `build_session_tools`) | Map matches code | no change |
| 3 | §7 trajectory step 4 ("capability model implemented; generic `/api/persona/<name>/turn` endpoint pending") ↔ code | Accurately states partial completion | no change |
| 4 | §2.5 ↔ §10 `Base` location (the PROCESS.md known example) — both now read `from infra.db import Base`; `infra/db.py:28` defines it | Consistent (baseline D-1 fix stuck) | no change |
| 5 | §2.3 topology — 7 sidecars, maestro +6, frontend-sandbox planned +7 ↔ `infra/topology.py:46-54` | Map matches code | no change |
| 6 | **`mobile/` (Expo/RN client, actively developed, modified this round) is absent from ARCHITECTURE.md** — the map documents only the `frontend`/`desktop` surface (§9) | code-drift (map silent on a real client surface) | **deferred → F11** |
| 7 | `CLAUDE.md` still says "6 sidecars … :8001..:8005" (7 exist, :8000–8006) | doc-stale | **note** (operator manual, out of ARCHITECTURE.md scope; baseline already flagged) |

**Verdict:** the architecture map matches the code on every authz/dispatch/topology/Base check
(the two reviewed commits kept the map honest, +72 lines). One real gap: the map omits `mobile/`
(F11). `CLAUDE.md`'s stale sidecar count is an operator-manual nit, carried as a note.

## Step 2 — Dimension scores (0–10)

| Part | D1 | D2 | D3 | Δ vs last | Note |
|---|---|---|---|---|---|
| `infra` | 9 | 9 | 9 | D1 ↑ (6→9) | F1 resolved; leaf clean. authz is a pure fn over a frozen dataclass (`authz.py:33-37`). Soft note: `ToolDomain` enum names personas at the leaf (by design). |
| `silicon_brain` | 9 | 8 | 7 | → | clean; on-read projection semantics take effort (`projections/__init__.py`). |
| `services/knowledge` | 9 | 9 | 9 | → | **F7 stays fixed** — `grep persona services/knowledge/` = data strings only, **0 import edges**; registration via `infra.user_data.load_domains()`. |
| `persona/teacher` | 9 | 7 | 8 | D3 ↑ (7→8) | session skills (`session_routing.md`, `session_control.md`) make "how the teacher leaves" legible; D3 moved, not D2. |
| `persona/app_operator` | 9 | 8 | 9 | **new** | reads no silicon_brain state → most testable persona; one-spec-per-verb. |
| `agents/frontend_engineer` | 9 | 7 | 8 | **new** | reached only via `request_ui_block`; uses a text FILES protocol, not `ToolSpec` → correctly has **no grant** (makes no selections to authorize). |
| `workshop/canvas` | 8 | **5** | 7 | **new** | all 10 verbs correctly `domain=CANVAS`; D2 dragged by **F8** (executors raise `NameError` and have no unit coverage) + heavy `mount_template` scaffolding. |
| `tools` (common) | 9 | 8 | 9 | **D1 ↑ (4→9), D2 ↑ (3→8)** | **F2 resolved** — `grep -rnE "^(from\|import) (persona\|services)\." tools/` = 0; reaches deps via infra seams only. (Was the worst part at baseline.) |
| `services/shell` | 9 | 9 | 9 | → | pure proxy + auth gate; authz/end-session needed **zero** route-table/topology change. |
| `services/persona` | 9 | 8 | 8 | **D1 ↑ (6→9), D3 ↑ (7→8)** | F3 fixed (brain via `SiliconBrainClient`; the lone `silicon_brain` import at `main.py:28` is import-time FK metadata, not a query). `ask.py` held at 459 lines — end-session extracted to `_ask_session.py` (91), no re-inflation. |
| `services/transcribe` | 9 | 8 | 9 | → | stateless; EOU fail-open 503. |
| `services/speak` | 9 | 8 | 8 | → | stateless Kokoro. |
| `services/browser` | 9 | 8 | 9 | D3 ↑ (8→9) | **F6 split landed** — `main.py` 1138→103 thin wiring. |
| `services/maestro` | 8 | 8 | 8 | → | clean `SiliconBrainClient`/`upstream_url`. Note: fresh client per feed request (minor churn). |
| `frontend` | 8 | 3 | 9 | → | F4 accepted (e2e-covered, not unit). |
| `desktop` | 9 | 5 | 9 | → | F5 accepted (e2e-covered). |
| `mobile` | 8 | 3 | 9 | **new** | clean single-API-layer consumer; go_home → store reset + fresh sessionId (`DynamicSurface.tsx:102-111`); e2e-only like frontend. |

- **Headline:** D1 min **8** (mean **8.8**) · D2 min **3** (mean **7.1**) · D3 min **7** (mean **8.5**)
- **Trend vs last ≥10 records** (only 2 prior rounds exist; baseline 2026-06-13 is the score reference):
  **broadly better.** D1 floor **4→8** as every baseline decoupling finding landed — `tools` 4→9 (F2),
  `infra` 6→9 (F1), `services/persona` 6→9 (F3). D3 mean **8.2→8.5** from F6 disassembly (browser,
  persona-sidecar) + teacher session skills. The **one downward signal** is `workshop/canvas` D2=5,
  dragged by F8 — but F8 is **pre-existing** (origin `31ee3b1`/`33eb5e1`, before this range), newly
  surfaced by the full sweep, not a regression from authz/end-session.
- **Vs ARCHITECTURE.md:** parts match their intended shape; the only map divergence is documentation
  (F11 mobile, and the CLAUDE.md note), not code drift.

## Step 3 — Open-issue triage

| Issue | Opened | Decision | Note / evidence |
|---|---|---|---|
| F1–F3, F6 | 2026-06-13 | **stay resolved** | re-verified by Step-0 greps + Step-2 score recoveries (tools/infra/persona-sidecar/browser). |
| F4, F5 | 2026-06-13 | **stay accepted** | frontend/desktop (and now mobile) covered via e2e, not unit — recorded coverage strategy. |
| F7 | 2026-06-17 | **stays resolved** | knowledge sidecar names **zero** persona code; 2nd persona addable without touching it (confirmed in Step 4). |

No prior finding silently regressed. New findings below.

## Step 4 — Ownership & protocol simulation

| Owner | Territory | Independent? | Leak / collision |
|---|---|---|---|
| infra | `infra/` (protocol provider) | ✓ | authz landed entirely within infra (3 files, add-only). Appending a `ToolDomain` is the one legit reason a persona owner edits infra. |
| silicon_brain | `silicon_brain/` + `services/knowledge/` | ✓ | clean; `SiliconBrainClient` signature changes ripple to 39 files (the blast-radius surface, unchanged this round). |
| teacher | `persona/teacher/` | ✓ | owns end_session/grants/skills; canvas-tool↔manifest registration is by-design coordination. |
| app_operator | `persona/app_operator/` | ✓ | shares the `AppAction` contract with teacher → **F9** (go_home string dup). |
| engineer | `agents/frontend_engineer/` | ✓ | text-FILES protocol; no grant needed. |
| canvas | `workshop/canvas/` | ✓ | all verbs domain-tagged; **F8** (missing `import json`) is a correctness leak in its executors. |
| maestro | `services/maestro/` | ✓ | clean; untouched this round. |
| shell | `services/shell/` + topology | ✓ | zero route-table/topology change required by authz/end-session. |
| frontend | `frontend/` + `desktop/` (+ `mobile/`) | ✓ | single API layer; mobile mirrors the pattern cleanly. |

**Protocol registry diff vs 2026-06-17 (P1–P11):** **P1–P11 UNCHANGED.** One **add-only** new element:

| # | Protocol element | Provider | Consumers | Change |
|---|---|---|---|---|
| **P12** | Tool authorization — `ToolDomain` enum + `ToolSpec.domain` (default `COMMON`) + `CapabilityGrant`/`authorize`/`authorized_tools` (`infra/model/authz.py`) + optional `grant` arg to `agent_loop.run` | infra (mechanism) + each persona (its grant values) | every persona manifest; `agent_loop` dispatch | **ADDED (add-only)** |

P12 is add-only-safe today (`ToolSpec.domain` defaults to `COMMON`; `agent_loop.run(grant=None)`
preserves pre-diff behavior — verified `infra/model/tools.py:55`, `agent_loop.py:203`). But it is now
off-limits to unilateral change: renaming/removing a `ToolDomain` member or altering `authorize`'s rule
silently breaks every persona's grant. Future rounds track it like `contracts/*`.

**Verdict — GREEN.** 9/9 owners independent; the protocol grew by one add-only element; **authz forced
no existing owner to change.** F7's payoff is real: a **new persona** (`helper`) can be added with its own
`grants.py` + `manifest.py` and **no edits to infra/authz, the knowledge sidecar, or another persona**.
Two residual new-persona registration seams remain: (1) a `route_addressee` branch in
`services/persona/routers/_ask_addressee.py:248-253` (unavoidable sidecar wiring), and (2) the
`AskRequest.addressee` `Literal` in `persona/teacher/schemas.py:21` — a **shared persona request DTO
living inside the teacher's package**, which forces a cross-persona edit → **F10**.

## New findings

### F8 — 16 tool executors call `json.*` without `import json` (runtime `NameError`, masked)
- **Dimension:** D2 (the no-unit-coverage gap is *why* it survived) — with a correctness symptom.
- **Severity:** **high**
- **Evidence:** 7 generic + 9 canvas tools use `json.dumps`/`json.loads` in their executor bodies with
  no module-level `import json` — e.g. `workshop/canvas/tools/block_action.py:51,56,66,67` (no json import
  at lines 10-17); also `tools/{search_notes,browser_set,read_url,look_at_image,look_at_video,web_view,read_document}.py`
  and `workshop/canvas/tools/{edit_note,read_media,point_arrow,interactive_graph,list_media,layout_blocks,push_block_content,request_ui_block}.py`.
  Runtime-proven: stub-loading `tools/search_notes.py` and calling `build_spec(uid).executor({})` raises
  `NameError: name 'json' is not defined`. It is **masked** by the blanket `except Exception` at
  `infra/model/agent_loop.py:79-80`, which re-serializes the failure as `{"error": "NameError: …"}` and hands
  it back to the persona LLM — for side-effecting tools (`block_action` SSE fan-out, `look_at_image` vision)
  the *effect* fires but the persona is told it failed and never gets the payload.
- **Origin:** **pre-existing** — the files were created/co-located in `33eb5e1` and `31ee3b1`, both before this
  round's range; the `d807495..d2ac2cc` diff only added the `domain=` tag and never touched the `json.*` calls.
  Surfaced by this full sweep, not a regression from authz/end-session.
- **Recommended action:** add `import json` to each of the 16 files (one line each). Then add a unit test that
  calls each tool's `build_spec(uid).executor(...)` with minimal args — the testability gap that hid this is
  the real D2 issue. Consider making `agent_loop`'s wrapper log/re-raise `NameError`/`AttributeError` (developer
  errors) instead of laundering them into tool-result strings, so the next such bug is loud.
- **Status:** ✅ resolved 2026-06-26 — added `import json` to all 16 modules (7 `tools/` + 9
  `workshop/canvas/tools/`), placed as the first stdlib import. Runtime-verified: `block_action`'s
  executor now returns its real `json.dumps` payload instead of `NameError`. Regression guard added —
  `tests/unit/test_tool_module_imports.py` (AST check: any tool module using `json.*` must `import json`;
  30 modules pass). The `agent_loop` blanket-except hardening (re-raise developer errors) is left as a
  separate optional follow-up, not done here. Per the snapshot convention, the `workshop/canvas` D2
  recovery (5→) shows as a trend next round.

### F9 — `go_home` action string hard-coded in two personas (DRY)
- **Dimension:** D1
- **Severity:** **low**
- **Evidence:** `persona/teacher/tools/end_session.py:49` (`AppAction(action="go_home")`, a composed effect)
  and `persona/app_operator/tools/app_action.py:97` both literal `"go_home"`; canonical `Literal` at
  `infra/contracts/ui.py:192`. The pydantic `Literal` catches a typo at construction, so the risk is
  rename-drift, not silent breakage. (Flagged as a non-blocking observation in the authz proposal.)
- **Recommended action:** add `GO_HOME`/`SWITCH_USER` constants next to `AppAction` in `infra/contracts/ui.py`
  and reference from both emit sites.
- **Status:** ✅ accepted 2026-06-26 (owner decision) — **won't-fix.** `go_home` is already defined
  exactly once, as the `Literal["switch_user", "go_home"]` type on `AppAction` (`infra/contracts/ui.py:192`)
  — the single source of truth. The two call sites are two personas legitimately *emitting the same shared
  contract value* ("one capability, two callers"), and the `Literal` makes any rename/typo fail loudly at
  construction (`ValidationError`), not silently. A `GO_HOME` constant would add indirection for no safety
  gain. Recorded as accepted, same disposition as F4/F5.

### F10 — shared persona request DTO `AskRequest` lives inside `persona/teacher/`
- **Dimension:** D1
- **Severity:** **low-medium**
- **Evidence:** `persona/teacher/schemas.py:21` defines `AskRequest` (incl. `addressee:
  Literal["teacher","frontend_engineer","app_operator"]`), consumed by `services/persona/routers/_ask_session.py:21`
  and `_ask_addressee.py`. It is a cross-persona wire type sitting in one persona's package — adding a new
  persona forces an edit to the teacher's file (the `addressee` Literal), and the sidecar router branch in
  `_ask_addressee.py:248-253`. This is the only thing keeping new-persona ownership from being fully clean.
- **Recommended action:** relocate the shared request DTO (and the `addressee` enum) to `infra/contracts/`
  so it's owned by the protocol layer, not a persona.
- **Status:** ✅ resolved 2026-06-26 — moved `AskRequest` (+ its `addressee` `Literal`) to a new
  `infra/contracts/ask.py`; repointed all 5 consumers (`services/persona/routers/{ask,_ask_session,_ask_addressee}.py`,
  `persona/teacher/contexts/{answer,_answer_parts}.py`) to `from infra.contracts.ask import AskRequest`;
  removed `AskRequest` and the now-unused `uuid4`/`Field` imports from `persona/teacher/schemas.py` (which
  now holds only the teacher-specific `SignalRequest`/`AskResponse`/`InteractionRead`). A new persona now
  imports the dispatch DTO from infra, never the teacher package — the `addressee` `Literal` it must extend
  also lives in infra now. The remaining `route_addressee` branch is unavoidable sidecar wiring, not a
  decoupling leak. Verified: dep-graph leaf clean, import smoke OK, 44 unit + 30 e2e-boundary green.

### F11 — `mobile/` client surface absent from `ARCHITECTURE.md`
- **Dimension:** D3 (map accuracy)
- **Severity:** **medium**
- **Evidence:** `mobile/` is a real, actively-developed Expo/React Native client (modified this round —
  `DynamicSurface.tsx`, `state/store.ts`, `lib/api/*`, `lib/session/voiceTurn.ts`), but a full-text search of
  `ARCHITECTURE.md` for "mobile"/"Expo"/"React Native" returns nothing; §9 documents only the Next.js+Electron
  surface. The map omits an entire client layer.
- **Recommended action:** add a brief "Client surfaces" section enumerating the three shell consumers
  (frontend, desktop, mobile), their shared contract, and the `app-action`/dynamic-stream dispatch model they
  all follow. (Report-only this round — deferred.)
- **Status:** open

## Notes (not findings)

- **`TYPE_CHECKING` imports compliant** — `persona/teacher/prompts/{answer,voice_answer,voice_brief}.py` import
  `silicon_brain` types under `if TYPE_CHECKING:` (allowed by invariant 3). Re-confirmed clean; recorded so a
  future round doesn't re-flag (same note as 2026-06-17).
- **`ToolDomain` at the leaf** — the enum names persona domains inside `infra/model/tools.py`. Deliberate (the
  capability registry belongs at the protocol provider); appending a domain is a coordinated change, not a leak.
- **maestro client churn** — `services/maestro/main.py:241-282` builds + `aclose`s a fresh `SiliconBrainClient`
  per feed request rather than a lifespan-shared pool (persona's pattern). Minor; tidy later.
- **tool→tool down-coupling** — `tools/read_document.py:21` and `tools/web_view.py:29` import
  `workshop.canvas.tools.{read_media,mount_template}`. Not a persona/services leak (F2's scope is clean), but a
  COMMON→CANVAS directional dependency worth watching.
- **`CLAUDE.md` stale sidecar count** — "6 sidecars … :8001..:8005" (7 exist). Operator manual, out of the
  ARCHITECTURE.md map's scope; left for a CLAUDE.md pass (same disposition as baseline).

## Notes — round meta

- This is the first round to combine a full Step-2 sweep with the Step-4 owner simulation in one pass; the
  per-owner adversarial lens again earned its place — F8 (a runtime bug) and F10 (a DTO-placement leak) were
  surfaced by owner simulation, not by dimension scoring alone.
- Protocol registry is now **P1–P12**; P12 (authz) is the only addition since 2026-06-17 and is add-only. An
  unchanged P1–P11 + all-owners-independent is the green signal that parallel work stayed safe across the
  authz/end-session work.
- Next round: re-run after F8 lands to watch `workshop/canvas` D2 move off 5; after F10 lands to confirm a new
  persona owner is fully clean (no teacher-file edit).
- **Same-round dispositions (2026-06-26, post-review, owner-directed):** **F8 resolved** (16 `import json`
  added + AST regression guard), **F10 resolved** (`AskRequest` lifted to `infra/contracts/ask.py`),
  **F9 accepted/won't-fix** (the `AppAction` `Literal` is already the single source of truth). **F11 stays
  open** (deferred — map doc). Net open after this round: **1 (F11)**. The scores above are the d2ac2cc
  snapshot and are left unchanged; F8/F10's effect surfaces as a trend next round.
- **Harness note (not a finding here):** `tests/unit/test_text_display_html.py` fails (not skips) in an
  environment that has `node` + the `.mjs` runner but no `frontend/node_modules` (missing `marked`). Its
  `skipif` guard checks for node + the runner file but not the npm dep — same worktree-missing-deps family as
  the e2e `.venv` skip. Pre-existing; worth a guard tweak (`skipif` should also check `marked` resolves).
