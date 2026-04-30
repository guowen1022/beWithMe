# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

beWithMe is a personalized reading assistant that uses episodic memory and a knowledge graph to tailor explanations to the user's background. Users paste passages, ask questions, and get answers informed by their profile, learning preferences, past interactions, and evolving concept mastery.

## Architecture

**Backend**: Six FastAPI processes — a thin shell at `services/shell/` (port `BASE_PORT`, default 8000) that proxies to five sidecars in `services/`: `persona`, `knowledge`, `transcribe`, `speak`, `browser`. The shell holds no DB session, no models, no Playwright.

**Control flow**: The frontend talks to the **persona sidecar** (the teacher), which decides which other services to call. Persona reads/writes silicon_brain state over HTTP via a typed client (`persona/teacher/silicon_brain_client.py`); it never imports silicon_brain ORM at runtime. Services feed persona; the relationship is never reversed.

The codebase is split into directional top-level packages. There is no `app/` — every layer has its own home:

- **`config.yaml`** (root) — only the truly cross-cutting deployment knobs: `base_port`, `service_host`. Loaded by `infra/topology.py`.
- **`infra/`** — stateless foundation. `auth.py` (header-only `parse_user_id`), `topology.py` (port + URL helpers), `config.py` (Ollama / LLM env), `contracts/` (DTOs shared on the wire between persona and silicon_brain), `hlr.py` (half-life regression math), `model/` (LLM provider facade), `rag/embedding.py`, `tools/web_fetch.py`. Imported by everyone, depends on no one.
- **`silicon_brain/`** — domain library: ORM models, persistence (`db.py` owns `Base`, `engine`, `async_session`, FastAPI `get_db`), `config.py` (`DATABASE_URL`), knowledge graph, brain_builder, retrieval. Depends on `infra` only. Reached by persona ONLY through the knowledge sidecar's HTTP API.
- **`persona/`** — top-level container for personas. Today only `persona/teacher/` exists (agent.py, prompt.py, prompt_v2.py, recommender/, session/, goals/, skills/, schemas.py, silicon_brain_client.py). At runtime the teacher imports only `infra` and the typed `SiliconBrainClient`; type hints into silicon_brain shapes are TYPE_CHECKING-guarded so they don't create runtime coupling. Future siblings (`persona/helper/`, etc.) live alongside.
- **`services/`** — six sidecars (shell, persona, knowledge, transcribe, speak, browser). HTTP routers live here, never inside `persona/`. Sidecar-local config (Whisper paths in `services/transcribe`, Kokoro paths in `services/speak`) lives with the sidecar. Depend on everything above.

All DB operations use async SQLAlchemy with asyncpg.
**Frontend**: Next.js (App Router) at `frontend/` — proxies `/api/*` to the backend via `next.config.ts` rewrites.
**Desktop**: Electron shell at `desktop/` — wraps the Next.js frontend and embeds a real Chromium browser pane alongside the reader. Same codebase targets macOS, Windows, and the web.
**Database**: PostgreSQL with pgvector extension for 768-dim vector similarity search.
**Embeddings**: Ollama running nomic-embed-text locally.
**LLM**: Anthropic SDK pointed at a configurable base URL (currently MiniMax API).

### Two-consumer architecture

The backend serves two consumers through three domain layers:

**`silicon_brain/`** (top-level, standalone) — The user's auto-profile (READ interface). Any agent can read this to understand the learner.
- `state.py` — `BrainState` facade: assembles full learner snapshot (profile + concepts + graph) for any agent
- `user_profile/` — Static preferences, preference embedding, session signals
  - `models.py` — `LearningPreferences` table: categorical labels + 768-dim `preference_embedding` vector
  - `ema.py` — Exponential Moving Average for preference embedding (`alpha=0.15`)
  - `preference_distiller.py` — LLM-based distillation of categorical preferences from interaction history
  - `state.py` — `UserProfileState` dataclass + `get_user_profile()` + `boost_query_embedding()`
- `knowledge/` — Dynamic learning state (concepts, mastery, graph)
  - `models.py` — `ConceptNode` (HLR mastery) + `ConceptEdge` (temporal relationships)
  - `hlr.py` — Half-Life Regression: mastery decays as `2^(-hours/half_life)`. States: solid > learning > rusty > faded
  - `concepts.py` — Concept extraction from `CONCEPTS:` line + HLR-aware upsert
  - `edges.py` — Temporal edges between co-occurring concepts
  - `graph.py` — NetworkX graph walks for prompt context
  - `visualize.py` — Graph data export for frontend

**`silicon_brain/brain_builder/`** — Builds and maintains the silicon brain (WRITE interface). Any agent feeds learnings through here.
- `ingester.py` — `AgentLearning` dataclass + `process_learning()`: generic entry point for any agent
- `concept_builder.py` — Concept extraction, HLR upsert, edge creation
- `preference_builder.py` — EMA preference updates, auto-distillation

**`persona/teacher/`** (top-level, was `app/teacher/`) — The teacher persona. Reads the silicon brain over HTTP via `silicon_brain_client.py`, generates personalized answers, feeds learnings back through the same client. Zero runtime `from silicon_brain` imports — only TYPE_CHECKING hints in `prompt.py` / `prompt_v2.py`. Future personas (helper, calendar, etc.) sit alongside as `persona/<name>/`.
- `agent.py` — `assemble_context(body, user_id, client)`: reads brain state via the client, builds `TeacherContext` for the LLM
- `prompt.py` — `build_answer_prompt()`: constructs the three-part cached prompt (system, passage, dynamic)
- `silicon_brain_client.py` — typed `httpx.AsyncClient` wrapper around the knowledge sidecar's APIs (brain-state, retrieval, recommendations, session-summaries, interactions, brain-builder)

### Key data flow

1. User submits a question via `POST /api/ask/stream` (SSE) — shell verifies auth, forwards to persona sidecar.
2. Persona's ask router (`services/persona/routers/ask.py`) calls `persona.teacher.agent.assemble_context(body, user_id, client)`.
3. Teacher reads brain state via `SiliconBrainClient` over HTTP to the knowledge sidecar:
   - `GET /api/brain-state` — composite read: profile, preferences, concepts, graph context
   - `POST /api/retrieval/document-chunks` — vector-search relevant chunks via pgvector
   - `POST /api/retrieval/past-summaries` — vector-search prior session summaries
   - `GET /api/sessions/{id}/interactions` — chronological history → multi-turn messages
4. Teacher builds a three-part prompt (`persona/teacher/prompt.py`): static_system (cached), passage (cached), dynamic (per-question).
5. Persona's ask router calls `infra.model.llm.stream_cached` directly (LLM is foundation, not a service) and streams the response.
6. After the answer completes, persona writes the interaction (`POST /api/interactions`) and fires the brain builder (`POST /api/brain-builder/post-interaction`); knowledge sidecar runs `silicon_brain.brain_builder.background.post_interaction_update` as a background task:
   - Embeds the interaction via Ollama
   - EMA-updates the preference embedding
   - Extracts concepts → upserts with HLR → creates temporal edges
   - Auto-distills categorical preferences if ≥10 new interactions

### Sidecar topology

Every sidecar binds to `BASE_PORT + offset` (offsets in `infra/topology.py:SERVICE_OFFSETS`):

| service | offset | runs |
|---|---|---|
| shell | +0 | pure HTTP proxy (CORS, auth gate, no DB, no models) |
| persona | +1 | `/api/ask`, `/api/interactions`, `/api/recommendations`, `/api/goals`, `/api/sessions/{id}/end` and `/api/sessions/summaries/graph`. The teacher's HTTP face. Owns the long-lived `SiliconBrainClient`. |
| knowledge | +2 | silicon_brain HTTP face — health, users, profile, preferences, concepts, documents, plus persona-facing read/write APIs (`/api/brain-state`, `/api/retrieval/*`, `/api/recommendations/*` (write), `/api/sessions/{id}/interactions`, `/api/sessions/summaries`, `/api/interactions` (write), `/api/brain-builder/post-interaction`) |
| transcribe | +3 | `/api/transcribe` (Whisper via pywhispercpp) |
| speak | +4 | `/api/speak`, `/api/speak/stream` (Kokoro TTS) |
| browser | +5 | `/api/browser/*` (Playwright); `/api/browser/render` is called by knowledge for `/api/documents/url` |

A deployer sets one env var (`BASE_PORT`) and the whole topology slides together — useful for running multiple environments side-by-side. Per-service overrides like `KNOWLEDGE_SERVICE_URL=http://other-host:9002` still win when set.

Inter-sidecar calls (besides what the shell proxies for the user):
- **persona → knowledge**: every brain read/write the teacher makes during a request.
- **browser → knowledge**: `/api/browser/resume` calls `/api/documents/from-extracted` to persist extracted pages.

### Other important patterns

- **LLM provider facade**: `infra/model/llm.py` is a thin re-export that picks `infra/model/minimax/` or `infra/model/deepseek/` based on `settings.llm_provider`. Call sites import from the facade and never reach into a provider directly. Each backend is a singleton module-level client.
- **Vector retrieval**: `silicon_brain/retrieval.py` searches interactions and document chunks by cosine similarity via pgvector. Queries are boosted with the user's preference embedding (70% query, 30% preference)
- **Document chunking**: 500-word chunks with 50-word overlap, embedded in background tasks
- **Persistence root**: `silicon_brain/db.py` owns `Base`, `engine`, `async_session`, and the FastAPI-shaped `get_db` async generator. No persistence module lives outside silicon_brain.
- **Agent-generic brain builder**: The `AgentLearning` dataclass accepts a `source` field — any future agent (helper, calendar, etc.) feeds learnings through the same `process_learning()` pipeline

### Desktop shell (`desktop/`)

Electron wraps the existing web UI in a native Mac window. One file: `desktop/src/main.ts` opens a `BrowserWindow` that loads `http://localhost:3000/` — the same route the web target serves. No desktop-specific UI, no custom chrome, no extra Next.js routes. Window size/position persists to `$userData/window.json`.

## Commands

### Backend
```bash
# Initialize database (creates DB, enables pgvector/uuid-ossp, creates tables)
python scripts/init_db.py

# Run all 6 sidecars (shell on :8000 by default; sidecars on :8001..:8005)
./scripts/dev-services.sh

# Slide the whole topology to a different base port
BASE_PORT=9000 ./scripts/dev-services.sh

# Run a single sidecar (it picks its port from BASE_PORT + its offset)
python -m services.transcribe          # → 8003
BASE_PORT=9000 python -m services.ask  # → 9001

# Run tests
pytest
```

### Frontend
```bash
cd frontend
npm install
npm run dev    # Dev server (port 3000)
npm run build  # Production build
npm run lint   # ESLint
```

### Desktop (Electron)
```bash
# Install desktop deps (one-time)
cd desktop && npm install && cd ..

# Dev: starts backend + next dev + Electron together
./scripts/dev-desktop.sh

# Mac .dmg
cd desktop && npm run dist:mac
```

### Benchmark
End-to-end integration benchmark in `benchmark/`. Drives the live FastAPI backend with
realistic reading/goal scenarios and saves timestamped results to `benchmark/results/`.
Whatever `LLM_PROVIDER` is active in `.env` is what the benchmark exercises — switch
providers by changing the env var and restarting the backend.
```bash
# Backend must be running (uvicorn) on :8000.
python -m benchmark --scenario 1          # reading + Q&A scenario
python -m benchmark --goal 1              # goal-planning scenario
python -m benchmark --scenario 2 --reset  # wipe DB first
```

## Environment Variables

Configured via `.env` in project root. Each module's `config.py` calls `load_dotenv()` at import,
so .env populates `os.environ` before any HTTP client is created — proxy vars
(`http_proxy`, `https_proxy`, `no_proxy`) end up where httpx / openai SDK / anthropic
SDK can see them. Provider URL/key/model fields have **no hardcoded defaults**; the
facade in `infra/model/llm.py` validates that the active provider's vars are set
and raises a clear `RuntimeError` at import otherwise.

Config homes:
- `config.yaml` (root) — `base_port`, `service_host`
- `silicon_brain/config.py` — `DATABASE_URL`
- `infra/config.py` — `OLLAMA_URL`, `EMBEDDING_*`, `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_*`, `DEEPSEEK_*`
- `services/transcribe/main.py` — `WHISPER_MODEL_PATH`, `WHISPER_THREADS`
- `services/speak/main.py` — `KOKORO_*`

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL async connection string (`postgresql+asyncpg://...`). |
| `OLLAMA_URL` | Ollama embedding server (default: `http://localhost:11434`). |
| `LLM_PROVIDER` | Active LLM backend: `minimax` or `deepseek` (default: `deepseek`). Dispatches to `infra/model/<provider>/llm.py`. |
| `LLM_MODEL` | Model name for the MiniMax-via-Anthropic provider (e.g. `MiniMax-M2.7-highspeed`). Required when `LLM_PROVIDER=minimax`. |
| `ANTHROPIC_API_KEY` | API key for the MiniMax-via-Anthropic provider. Required when `LLM_PROVIDER=minimax`. |
| `ANTHROPIC_BASE_URL` | Custom Anthropic-compatible endpoint (e.g. MiniMax). Required when `LLM_PROVIDER=minimax`. |
| `DEEPSEEK_API_KEY` | API key for the DeepSeek provider. Required when `LLM_PROVIDER=deepseek`. |
| `DEEPSEEK_BASE_URL` | DeepSeek base URL (e.g. `https://api.deepseek.com`). Required when `LLM_PROVIDER=deepseek`. |
| `DEEPSEEK_MODEL` | DeepSeek model name (e.g. `deepseek-v4-pro`). Required when `LLM_PROVIDER=deepseek`. |
| `no_proxy` | Comma-separated hosts that bypass `http_proxy`/`https_proxy`. Leading dot = subdomain match. e.g. `api.deepseek.com,.minimaxi.com`. |

## Prerequisites

- PostgreSQL with pgvector extension
- Ollama running locally with `nomic-embed-text` model pulled
- Python 3.9+ with dependencies from `requirements.txt`
- Node.js for the frontend

## Frontend Note

The frontend uses a custom Next.js version with breaking changes. Always read `node_modules/next/dist/docs/` before modifying Next.js-specific code (see `frontend/AGENTS.md`).

## TODO Policy

When implementing a feature using a workaround or hack that is likely to be replaced by a built-in API in the future (e.g. a library or framework adding native support), add a line to `TODO.md` in the project root. Format:

```
- [urgency/10][module-name] Description of the hack and what should replace it.
```

- **Urgency**: 1/10 = low priority (works fine, just ugly), 10/10 = critical (blocking or fragile)
- **Module**: the area of the codebase, e.g. `frontend/pdf-viewer`, `backend/brain-builder`
- **Description**: what the hack does, why it exists, and what built-in API would replace it
