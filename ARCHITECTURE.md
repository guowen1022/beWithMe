# Architecture

> This document is the source of truth for **how beWithMe is structured and why**. Any agent making structural changes (new module, new sidecar, new dependency, new persona) must read this file first.
>
> `CLAUDE.md` is the operator's manual (how to run, where logs live, command reference). Keep ops there; keep architecture here.

---

## 1. Vision in one paragraph

beWithMe is a multi-persona, LLM-driven assistant. The user is not a participant in the runtime control flow — the user is the *audience*. **Personas** (teacher, helper, engineer, …) are LLM-backed actors that decide what to do. They invoke **tools** (typed wrappers around services and frontend mutations) to act. Services are dumb capability providers (DB, STT, TTS, web fetch, UI sandbox). The user's identity, knowledge, and history live in the **silicon brain**. Everything stateless and shared (LLM, embedding, contracts, HTTP topology) lives in **infra**. One persona may delegate to another (e.g., teacher → engineer to build a UI on the fly). The user's interface itself is mutable at runtime: a persona can replace a widget, open a new tab, or hand the user a freshly generated page — but never directly. UI changes pass through a **frontend sandbox** that validates syntax and behavior before delivery.

---

## 2. Layers

The system has five layers. Each layer can only depend *downward*.

```
┌──────────────────────────────────────────────────────────────────────┐
│  USER  (audience — not a participant in control flow)                │
└────────────────────────────────────────┬─────────────────────────────┘
                                         ↑↓ voice, text, UI events
┌──────────────────────────────────────────────────────────────────────┐
│  PERSONA  (the LLM-driven decision layer)                            │
│  persona/teacher/, persona/helper/, persona/engineer/, …             │
│  Owns: tone, judgment, tool selection, memory access policy          │
└────────────────────────────────────────┬─────────────────────────────┘
                                         ↑↓ typed tool calls
┌──────────────────────────────────────────────────────────────────────┐
│  TOOLS  (verbs the persona can do — wrap services + UI mutations)    │
│  tools/  — one Python module per tool, typed schema, uniform shape   │
│  Owns: the mapping from "what an LLM wants" to "what a service does" │
└────────────────────────────────────────┬─────────────────────────────┘
                                         ↑↓ HTTP
┌──────────────────────────────────────────────────────────────────────┐
│  SERVICES  (capability sidecars — never decide, only execute)        │
│  services/{shell, knowledge, transcribe, speak, browser, …}          │
│  Owns: DB writes, model inference, browser control, UI sandbox       │
└──────────────────────────────────────────────────────────────────────┘

                          (silicon_brain) and (infra) are the foundation
                          everyone above can use.
```

### 2.1 Persona — the decision layer

A persona is an LLM with a name, a system prompt, a memory access policy, and a tool registry. It is the only component that *decides* anything at runtime. The rules:

- Each persona lives at `persona/<name>/` (`persona/teacher/` exists today; `persona/helper/`, `persona/engineer/`, etc. follow the same shape).
- A persona reads/writes the silicon brain **only over HTTP** (via `SiliconBrainClient`). No `from silicon_brain.*` imports at runtime — TYPE_CHECKING-only hints are allowed.
- A persona invokes capabilities **only via tools**. No direct service URL calls in persona code.
- Personas can call other personas (teacher → engineer for "build me a UI").
- A persona never imports another persona's internals; cross-persona calls use the same tool/HTTP boundary.

### 2.2 Tools — the verbs

A tool is a typed function the LLM is allowed to invoke. Each tool wraps one service capability or one UI mutation in a uniform schema:

```python
class Tool:
    name: str                  # e.g. "end_session"
    description: str           # for the LLM's tool selection
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    async def call(self, args, ctx) -> output_schema: ...
```

Examples (target state):
- `end_session(session_id)` — calls knowledge sidecar, marks session ended, kicks summarizer.
- `speak_text(text, voice?)` — calls speak sidecar.
- `transcribe_audio(blob, language?)` — calls transcribe sidecar.
- `retrieve_document_chunks(document_id, query)` — calls knowledge.
- `open_browser_tab(url)` — calls browser sidecar.
- `update_widget(widget_id, content)` — calls frontend-sandbox; only available to the engineer persona.
- `replace_page(route, component_source)` — engineer-only; routes through the sandbox.

Tools are the *only* way a persona acts on the world. The frontend talking directly to a service URL is legacy and is being phased out (see § 7 *Migration state*).

### 2.3 Services — the capability sidecars

Services don't decide; they execute. Each service is a FastAPI process at a fixed port offset (see `infra/topology.py:SERVICE_OFFSETS`). The shell at offset +0 is the only public face; everything else is reachable from within the trusted network.

| service | offset | role |
|---|---|---|
| shell | +0 | reverse proxy, auth gate (`X-User-Id` verify, 60s TTL cache), CORS |
| persona | +1 | the persona's HTTP face — hosts persona endpoints today; will host the generic dispatch tomorrow |
| knowledge | +2 | silicon-brain HTTP face (CRUD + read/write APIs persona consumes) |
| transcribe | +3 | local Whisper |
| speak | +4 | local Kokoro |
| browser | +5 | Playwright (headless web fetch + handoff) |
| **frontend-sandbox** (planned) | +6 | runtime UI generation + validation, see § 5 |

Routers always live in `services/`. Never in persona, never in silicon_brain, never in infra.

### 2.4 silicon_brain — the user's persistent memory

silicon_brain holds **the user's data and only the user's data**. It is not "the database" — it is "the user's brain": their memory, their acts, their preferences, their evolving knowledge graph. Every table in silicon_brain has a `user_id` and describes a slice of one specific person.

- `models/` — user-scoped ORM tables: `User`, `Profile`, `Interaction`, `Document`, `DocumentChunk`, `ConceptNode`, `ConceptEdge`, `LearningPreferences`, `LearningGoal`, `Recommendation`, `SessionSummary`. Every row belongs to exactly one user.
- `knowledge/` — operations on the user's concept graph (concept extraction, edges, walks).
- `user_profile/` — operations on the user's preferences (EMA blend, LLM-driven distillation).
- `brain_builder/` — write-side: ingester + concept/preference builders + the post-interaction background pipeline.
- `retrieval.py` — vector queries over the user's interactions and documents.
- `state.py` — `BrainState` composite read.

silicon_brain does **not** own the persistence machinery (Base, engine, session factory). That belongs in `infra/` (see §2.5). silicon_brain only declares its own models on the shared Base.

silicon_brain does not import from any layer above it. It is exposed to personas via the **knowledge sidecar's HTTP API** — never imported directly by persona code.

#### What does NOT go in silicon_brain

If the data isn't about a specific user, it doesn't go here. Examples:
- A persona's own self-memory (e.g., teacher's accumulated record of which analogies work) → `persona/teacher/models/`
- An engineer persona's library of validated UI components → `persona/engineer/models/`
- A service's runtime cache or quota counters → `services/<name>/models/` (or in-memory if appropriate)
- Generic reference data (taxonomies, lookup tables that aren't user-personal) → wherever the consuming domain lives

Each domain owns its own persistence for its own data. The shared Base in infra ensures they all live in the same Postgres database — the boundary is conceptual (which package declares the model), not physical (separate DBs).

### 2.5 infra — the stateless foundation + persistence machinery

infra is the toolbox everyone can use. Stateless utilities + the **shared persistence engine**. No domain knowledge, no upward imports.

- `db.py` — `Base = DeclarativeBase`, `engine`, `async_session`, `get_db`. **The single persistence root for the whole project.** Every domain (silicon_brain, persona, future services) declares its ORM models on this Base. One Postgres database, one engine, many domains.
- `auth.py` — `parse_user_id` (header parse only)
- `topology.py` — `SERVICE_OFFSETS`, `upstream_url`, route table
- `config.py` — `DATABASE_URL`, Ollama URL, embedding model, LLM provider env
- `contracts/` — DTOs shared on the wire between domains over HTTP
- `hlr.py` — half-life regression math
- `model/` — LLM provider facade (deepseek + minimax)
- `rag/embedding.py` — Ollama embeddings
- `tools/web_fetch.py` — Playwright + trafilatura helper

> **Why Base lives in infra, not silicon_brain.** silicon_brain is a *consumer* of persistence, not its owner. If persona/teacher needs to declare its own tables (e.g., a `TeacherStrategy` table for self-memory), it must inherit from a Base that doesn't sit "above" it in the dep graph. Putting Base in infra (the leaf) lets every domain build on it without violating the one-direction rule.

---

## 3. Dependency graph

One direction, no cycles. Enforced by grep at every refactor.

```
infra  ←  silicon_brain  ←  services/*
   ↖           ↑                ↑
    ──── persona/* ←────────────┘
            (persona NEVER imports silicon_brain at runtime;
             persona uses SiliconBrainClient over HTTP)
```

Each non-infra domain may have its own persistence. They all inherit from `infra.db.Base`. silicon_brain declares user-scoped tables; personas may declare persona-private tables under `persona/<name>/models/`; services may declare service-private tables under `services/<name>/models/`. None of these "see" each other's tables.

**Static checks** (must each return zero):

```bash
# infra is the leaf — never imports up the graph
grep -rnE "^(from|import) (app|silicon_brain|persona|services)\." infra/

# silicon_brain depends only on infra
grep -rnE "^(from|import) (app|persona|services)\." silicon_brain/

# persona has no runtime silicon_brain imports
# (TYPE_CHECKING-guarded imports in `if TYPE_CHECKING:` blocks are allowed)
grep -rnE "^(from|import) silicon_brain" persona/

# persona-A doesn't import persona-B internals
# (cross-persona calls happen via tools, not imports)
grep -rnE "^(from|import) persona\.(?!<self>)" persona/<each-persona>/
```

The persona/silicon_brain rule is the strict one. The reason: personas must remain *swappable* and *testable* against a fake silicon_brain (e.g., when running an `engineer` persona that's experimenting with new UI). The HTTP boundary makes that mockable for free.

A persona *may* read its own `persona/<self>/models/` tables directly via SQLAlchemy on `infra.db`. That's persona-private data; no HTTP indirection needed.

---

## 4. The persona model in detail

### 4.1 What lives in `persona/<name>/`

A persona is a self-contained module with this shape:

```
persona/<name>/
├── __init__.py          # public surface: agent.assemble_context, persona.respond, …
├── agent.py             # the loop: read state → think → choose tools → act → write state
├── prompt.py            # builds the system + user prompt parts
├── schemas.py           # request/response DTOs the persona's HTTP face exposes
├── silicon_brain_client.py
│                        # typed HTTP client for the knowledge sidecar (shared by
│                        # all personas; will move to infra once a 2nd persona exists)
├── skills/              # markdown skill prompts loaded at runtime
└── <subdomain>/         # e.g. teacher/recommender/, teacher/session/
```

A persona has no FastAPI router. Its HTTP face lives in `services/persona/routers/`.

### 4.2 What makes one persona different from another

- **Tool allowlist**: teacher can `speak_text`, `end_session`, `recommend`, `retrieve_chunks`, … but not `replace_page`. Engineer can `replace_page`, `update_widget`, `compile_component`, but not `end_session`. The allowlist is part of each persona's wiring, not enforced inside the tool.
- **System prompt**: the persona's voice, judgment style, refusal patterns.
- **Memory access policy**: which slices of the brain it reads/writes. Teacher reads everything, writes interactions + concepts via `brain_builder`. Engineer might only read the user's preference embedding (for UI personalization) and write nothing.

### 4.3 Persona-to-persona calls

When teacher needs the engineer to build a page, teacher calls a tool (e.g., `request_ui_component(spec)`). That tool dispatches to the engineer persona — which is just another persona running in the same process or a sibling sidecar. Persona A never imports persona B's Python code. The boundary is the same tool/HTTP one as user → persona.

---

## 5. Frontend dynamic (planned)

> Status: not implemented. Tracked here so the architecture has a slot for it. Reference: the user's separate PoC for frontend-dynamic component generation.

### 5.1 The need

A persona may decide that the best response is a **page**, not a paragraph. Examples:
- "Show me a flashcard for ATP synthesis" → engineer compiles a `<Flashcard>` widget.
- "Open a side panel with my goals" → engineer mounts a panel into the current layout.
- "Replace the reader with a quiz on what I just read" → engineer swaps the route.

### 5.2 The sandbox layer

UI mutations don't ship to the user directly. They flow through a sandbox:

```
engineer persona → tool: replace_widget(spec) →
  services/frontend-sandbox/  (new sidecar at offset +6)
    1. Receive component spec / source.
    2. Compile (TypeScript/JSX → JS bundle).
    3. Lint + type-check.
    4. Run smoke render (jsdom or Playwright).
    5. If pass: emit "ui-update" event over SSE/WS to the live frontend.
    6. If fail: return error to engineer persona; engineer may retry with the error.
```

The sandbox is a **gate**, not a deployment system. It exists so a buggy LLM-generated component can't crash the user's session.

### 5.3 Where it lives

- **`services/frontend-sandbox/`** — the sidecar.
- **`sandbox/`** (top-level, planned) — the working tree where compiled artifacts land for inspection. `.gitignore`'d.
- **`infra/contracts/ui.py`** (planned) — the typed `WidgetSpec`, `ComponentSource`, `UIUpdate` DTOs.

The frontend itself gains a small "dynamic surface" registry (`frontend/lib/dynamic.ts`) that listens for `ui-update` events and mounts/replaces components. Static UI keeps working; dynamic components layer on top.

### 5.4 Out-of-frame surfaces — the `web_view` block

Most blocks are pure DOM: their entire contents render inside the React frontend's origin and the sandbox can inspect/validate them. The `web_view` block is a deliberate exception.

- The block's own DOM is just a header strip + a transparent body — that's what the sandbox sees and validates.
- The actual page contents render in a separate Chromium top-level context (the Electron `WebContentsView` / `BrowserView` defined in `desktop/src/main.ts`), positioned over the block's body rect.
- The persona's `web_view` tool drives navigation and perception via a token-authed HTTP shim (`desktop/src/web_view_shim.ts`) — not via the sandbox.

**Why this is safe**: the sandbox guards against an LLM-generated component crashing or exfiltrating from the user's session. The `web_view` block is *not* LLM-generated — it's a fixed template the persona only mounts/unmounts; it does not author its own code. The page contents are explicitly sandboxed by Chromium itself (separate origin, separate cookie jar via `partition: "persist:bewithme-browser"`, separate process). The trust boundary moves from "our sandbox" to "Chromium's site isolation," which is exactly the model that makes a real browser safe.

**Why this is needed**: many real pages refuse to render inside an iframe (anti-embedding via `X-Frame-Options` / `frame-ancestors`, or the page's own JS detecting framing and refusing to fetch). Even when an iframe loads, third-party storage partitioning means session-bound SPAs can't authenticate. A separate top-level Chromium context — first-party cookies, real `Referer`, no `window.top` self-checks — is the only correct fix.

This is the only block today that lives outside the sandbox. Other personas authoring novel widgets (engineer's `request_new_block`) still go through the sandbox unchanged.

---

## 6. Authentication & trust

- The **shell** is the only public-facing process. It verifies `X-User-Id` against the knowledge sidecar's `/api/auth/verify` endpoint, caches the result for 60s, then forwards.
- All other sidecars run on the trusted network. They trust whatever `X-User-Id` they receive.
- **Public paths** (no auth needed): `/`, `/api/health`, `GET /api/users`, `POST /api/users`. Used during user bootstrap.
- Inter-sidecar calls forward `X-User-Id` unchanged.

This trust model is appropriate for single-machine dev and small private deploys. For internet-facing deployments, swap shell-side cookie verification for a signed-token scheme (out of scope until needed).

---

## 7. Migration state — current vs vision

The current codebase implements the foundation but not the full vision. Don't be confused by the gap.

### What is in the vision

| Layer | Element | Status | Notes |
|---|---|---|---|
| infra | auth, topology, contracts, model, rag, tools/web_fetch | ✅ | clean leaf, dep-graph rules enforced |
| infra | persistence machinery (Base, engine, session, get_db) at `infra/db.py` | ✅ | shared root; every domain inherits from `infra.db.Base` |
| infra | DATABASE_URL in `infra/config.py` | ✅ | one Postgres URL, shared across domains |
| silicon_brain | shrunk to neutral user data (User, Profile, Document, DocumentChunk, UserPreferences) | ✅ | every table has `user_id`; teacher-authored data lives in persona/teacher/ |
| silicon_brain | exposed via knowledge sidecar HTTP face | ✅ | persona reads it via the narrow `SiliconBrainClient` (3 methods) |
| Persona | teacher | ✅ | runtime decoupled from silicon_brain; reads its own data via direct DB |
| Persona | per-persona own-tables (teacher owns Interaction, ConceptNode, LearningGoal, Recommendation, LearningSession, TeacherPreferenceModel) | ✅ | declared on `infra.db.Base`; queried directly without HTTP |
| Persona | helper | ❌ | placeholder dir TBD |
| Persona | engineer | ❌ | needed for frontend-dynamic |
| Persona | tool registry per persona | ❌ | persona-side tool dispatch not yet implemented |
| Tools | tools/ top-level package | ❌ | tools currently live as static FastAPI routers in `services/persona/routers/` |
| Tools | typed Tool protocol | ❌ | DTOs exist (`infra/contracts/`) but no `Tool` interface |
| Services | shell, persona, knowledge, transcribe, speak, browser | ✅ | all 6 sidecars running |
| Services | frontend-sandbox | ❌ | not built |
| Frontend | static REST → backend | ✅ | works today |
| Frontend | dynamic UI mutation by personas | ❌ | needs sandbox + dispatcher |

### The intermediate state today

- The frontend talks to **persona endpoints directly** via fixed URLs (`/api/ask`, `/api/recommendations`, etc.). The persona sidecar's routers live at `services/persona/routers/` and act as static glue. This is the legacy path.
- Personas don't yet pick from a tool registry; their behavior is hardcoded in `agent.py`.
- The user has only one persona (teacher) so there's no inter-persona dispatch to test.

### The trajectory

1. ✅ **Persistence root moved to infra** — `infra/db.py` owns `Base`, `engine`, `async_session`, `get_db`. `DATABASE_URL` lives in `infra/config.py`. Every domain inherits from the shared Base.
2. **Define the `Tool` protocol** in `infra/tools_protocol.py` (or `tools/__init__.py`). Typed input/output schemas; `async def call(...)` shape.
3. **Wrap existing service calls as tools** under `tools/<name>.py`. Each tool calls a service via HTTP; reuses `infra/contracts/` DTOs for I/O.
4. **Bind tools to personas** via per-persona allowlists. Add a generic dispatch endpoint `POST /api/persona/<name>/turn` that takes the user's message, lets the LLM pick a tool, executes it, returns the result (and any UI updates).
5. **Collapse static routers** in `services/persona/routers/*` into the generic dispatch. The shell continues forwarding to the persona sidecar at the same offset.
6. **Add `services/frontend-sandbox/`** as the +6 sidecar. Build the engineer persona on top of it.
7. **Add `persona/helper/` and `persona/engineer/`** as siblings. When/if a persona needs persona-private state, add `persona/<name>/models/` and inherit from `infra.db.Base`.

Each step is independent and ships green.

---

## 8. Decision rules — "where does this go?"

Use this table when adding code. If your change doesn't fit, the architecture probably needs an explicit conversation, not silent sprawl.

| Change | Goes in | Why |
|---|---|---|
| New ORM table for **user data** (per-user, has `user_id`) | `silicon_brain/models/<name>.py` | silicon_brain owns the user's brain |
| New ORM table for a **persona's own state** (no `user_id`, persona-private) | `persona/<name>/models/<table>.py` | each domain owns its own persistence |
| New ORM table for a **service's own state** (cache, quota, audit log) | `services/<name>/models/<table>.py` | service-private data |
| Shared persistence machinery (Base, engine, session, get_db) | `infra/db.py` | the leaf — every domain inherits Base from here |
| New brain-state read endpoint (over user data) | `services/knowledge/routers/<name>.py` + `SiliconBrainClient` method + `infra/contracts/` DTO | the 4-file pattern |
| New brain-state write endpoint | same as above; persistence in knowledge router; persona never writes user data via ORM |
| New stateless utility (no DB, no domain) | `infra/<file>.py` | infra is the leaf |
| New persona | `persona/<name>/` (sibling to teacher/) + tool allowlist + system prompt + optional `models/` |
| New tool (verb a persona can call) | `tools/<name>.py` (planned location) | uniform schema, typed |
| New sidecar service | `services/<name>/` + offset in `infra/topology.py:SERVICE_OFFSETS` + entry in shell's `PREFIX_TO_SERVICE` |
| New frontend route or page | `frontend/app/<route>/page.tsx` |
| New persistent UI mutation by an LLM | `services/frontend-sandbox/` (planned); engineer-persona-only tool |
| New env var consumed by infra (DB URL, LLM, embedding, …) | `infra/config.py` |
| Sidecar-local config (model paths, daemon flags) | inside the sidecar (`services/<name>/main.py`) — not shared |
| Cross-cutting topology knob | `config.yaml` (root) — only `base_port`, `service_host` belong here today |

---

## 9. Invariants (the one-line laws)

These are the rules every refactor must preserve. If a PR violates one, reject it.

1. `infra/` imports nothing from `silicon_brain/`, `persona/`, or `services/`.
2. `silicon_brain/` imports nothing from `persona/` or `services/`.
3. `persona/<any>/` has zero **runtime** imports from `silicon_brain/`. TYPE_CHECKING-guarded imports for type hints are allowed.
4. `persona/<A>/` does not import `persona/<B>/` internals. Cross-persona calls go through tools / HTTP.
5. **Persistence machinery (Base, engine, sessions) lives in `infra/db.py`.** No domain owns it.
6. **silicon_brain holds user-scoped data only.** Every silicon_brain table has a `user_id`. Non-user data (a persona's self-memory, a service's runtime state) belongs in that domain's own `models/`.
7. Persona reaches the silicon brain only via `SiliconBrainClient` (HTTP). A persona may directly query its own `persona/<self>/models/` tables via SQLAlchemy on `infra.db`.
8. Routers live only in `services/<name>/routers/`. Domain packages have no FastAPI code.
9. Every public endpoint requires `X-User-Id` unless explicitly listed in `services/shell/auth.py:PUBLIC`.
10. Every wire payload between domains uses a DTO from `infra/contracts/`. No raw dicts.
11. A new sidecar gets a fixed offset in `infra/topology.py:SERVICE_OFFSETS`. Order matters; only append.
12. Sidecar-local config (model paths, daemon flags) lives with the sidecar, not in any shared config module.
13. UI mutations driven by an LLM go through the sandbox. Direct LLM-to-DOM is forbidden.

---

## 10. Verification commands

Run these after any structural change:

```bash
# Dep graph (zero hits each)
grep -rnE "^(from|import) (app|silicon_brain|persona|services)\." infra/
grep -rnE "^(from|import) (app|persona|services)\." silicon_brain/
grep -rnE "^(from|import) silicon_brain" persona/   # exclude TYPE_CHECKING blocks manually

# E2E suite
.venv/bin/python -m pytest tests/e2e/ -v

# Import smoke
.venv/bin/python -c "
from infra.contracts import BrainStateDTO, RecommendationDTO
from infra.hlr import compute_mastery
from silicon_brain.db import Base, engine, async_session, get_db
import persona.teacher
from persona.teacher.silicon_brain_client import SiliconBrainClient
import services.persona.main, services.knowledge.main
print('OK')
"
```
