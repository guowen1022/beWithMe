# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

beWithMe is a personalized reading assistant that uses episodic memory and a knowledge graph to tailor explanations to the user's background. Users paste passages, ask questions, and get answers informed by their profile, learning preferences, past interactions, and evolving concept mastery.

## Architecture

**Backend**: Six FastAPI processes — a thin shell at `services/shell/` (port `BASE_PORT`, default 8000) that proxies to five sidecars in `services/`: `ask`, `knowledge`, `transcribe`, `speak`, `browser`. The shell holds no DB session, no models, no Playwright. Domain logic, ORM models, and provider facades live in the shared library at `app/` (imported by every sidecar). All DB operations use async SQLAlchemy with asyncpg.
**Frontend**: Next.js (App Router) at `frontend/` — proxies `/api/*` to the backend via `next.config.ts` rewrites.
**Desktop**: Electron shell at `desktop/` — wraps the Next.js frontend and embeds a real Chromium browser pane alongside the reader. Same codebase targets macOS, Windows, and the web.
**Database**: PostgreSQL with pgvector extension for 768-dim vector similarity search.
**Embeddings**: Ollama running nomic-embed-text locally.
**LLM**: Anthropic SDK pointed at a configurable base URL (currently MiniMax API).

### Two-consumer architecture

The backend serves two consumers through three domain layers:

**`app/silicon_brain/`** — The user's auto-profile (READ interface). Any agent can read this to understand the learner.
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

**`app/brain_builder/`** — Builds and maintains the silicon brain (WRITE interface). Any agent feeds learnings through here.
- `ingester.py` — `AgentLearning` dataclass + `process_learning()`: generic entry point for any agent
- `concept_builder.py` — Concept extraction, HLR upsert, edge creation
- `preference_builder.py` — EMA preference updates, auto-distillation

**`app/teacher/`** — The teacher agent. Reads the silicon brain, generates personalized answers, feeds learnings back.
- `agent.py` — `assemble_context()`: reads brain state, builds `TeacherContext` for the LLM
- `prompt.py` — `build_answer_prompt()`: constructs the three-part cached prompt (system, passage, dynamic)

### Key data flow

1. User submits a question via `POST /api/ask/stream` (SSE)
2. Teacher agent assembles context (`teacher/agent.py`):
   - Reads brain state: profile, preferences, concepts, graph context
   - Retrieves relevant document chunks via pgvector
   - Builds session history as multi-turn messages
3. Teacher builds a three-part prompt (`teacher/prompt.py`): static_system (cached), passage (cached), dynamic (per-question)
4. LLM generates a streaming response (with `CONCEPTS:` line at the end)
5. Background task feeds the brain builder (`brain_builder/ingester.py`):
   - Embeds the interaction via Ollama
   - EMA-updates the preference embedding
   - Extracts concepts → upserts with HLR → creates temporal edges
   - Auto-distills categorical preferences if ≥10 new interactions

### Sidecar topology

Every sidecar binds to `BASE_PORT + offset` (offsets in `services/shell/proxy.py:SERVICE_OFFSETS`):

| service | offset | runs |
|---|---|---|
| shell | +0 | pure HTTP proxy (CORS, no DB, no models) |
| ask | +1 | `/api/ask`, `/api/interactions` (LLM, teacher agent) |
| knowledge | +2 | health, users, profile, preferences, concepts, sessions, documents, goals, recommender |
| transcribe | +3 | `/api/transcribe` (Whisper via pywhispercpp) |
| speak | +4 | `/api/speak`, `/api/speak/stream` (Kokoro TTS) |
| browser | +5 | `/api/browser/*` (Playwright); also `/api/browser/render` is called by knowledge for `/api/documents/url` |

A deployer sets one env var (`BASE_PORT`) and the whole topology slides together — useful for running multiple environments side-by-side. Per-service overrides like `KNOWLEDGE_SERVICE_URL=http://other-host:9002` still win when set.

The browser sidecar's `/api/browser/resume` calls knowledge's internal `/api/documents/from-extracted` to persist extracted pages; that's the only inter-sidecar call other than what the shell proxies.

### Other important patterns

- **LLM provider facade**: `app/infra/model/llm.py` is a thin re-export that picks `app/infra/model/minimax/` or `app/infra/model/deepseek/` based on `settings.llm_provider`. Call sites import from the facade and never reach into a provider directly. Each backend is a singleton module-level client.
- **Vector retrieval**: `app/infra/rag/retrieval.py` searches interactions and document chunks by cosine similarity via pgvector. Queries are boosted with the user's preference embedding (70% query, 30% preference)
- **Document chunking**: 500-word chunks with 50-word overlap, embedded in background tasks
- **`app/db_base.py`**: Shared SQLAlchemy `Base` class, extracted to avoid circular imports between modules
- **Re-export stubs**: `app/models/preferences.py` and `app/models/concept.py` re-export from `silicon_brain/` sub-modules, preserving backward-compatible import paths
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

Configured via `.env` in project root. `app/config.py` calls `load_dotenv()` at import,
so .env populates `os.environ` before any HTTP client is created — proxy vars
(`http_proxy`, `https_proxy`, `no_proxy`) end up where httpx / openai SDK / anthropic
SDK can see them. Provider URL/key/model fields have **no hardcoded defaults**; the
facade in `app/infra/model/llm.py` validates that the active provider's vars are set
and raises a clear `RuntimeError` at import otherwise.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL async connection string (`postgresql+asyncpg://...`). |
| `OLLAMA_URL` | Ollama embedding server (default: `http://localhost:11434`). |
| `LLM_PROVIDER` | Active LLM backend: `minimax` or `deepseek` (default: `deepseek`). Dispatches to `app/infra/model/<provider>/llm.py`. |
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
