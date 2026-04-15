# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

beWithMe is a personalized reading assistant that uses episodic memory and a knowledge graph to tailor explanations to the user's background. Users paste passages, ask questions, and get answers informed by their profile, learning preferences, past interactions, and evolving concept mastery.

## Architecture

**Backend**: Python FastAPI (async) at `app/` — all DB operations use async SQLAlchemy with asyncpg.
**Frontend**: Next.js (App Router) at `frontend/` — proxies `/api/*` to the backend via `next.config.ts` rewrites.
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

### Other important patterns

- **Singleton LLM client**: `app/services/llm.py` uses a module-level client; `anthropic_base_url` in config redirects to a proxy/alternative provider
- **Vector retrieval**: `app/services/retrieval.py` searches interactions and document chunks by cosine similarity via pgvector. Queries are boosted with the user's preference embedding (70% query, 30% preference)
- **Document chunking**: 500-word chunks with 50-word overlap, embedded in background tasks
- **`app/db_base.py`**: Shared SQLAlchemy `Base` class, extracted to avoid circular imports between modules
- **Re-export stubs**: `app/models/preferences.py` and `app/models/concept.py` re-export from `silicon_brain/` sub-modules, preserving backward-compatible import paths
- **Agent-generic brain builder**: The `AgentLearning` dataclass accepts a `source` field — any future agent (helper, calendar, etc.) feeds learnings through the same `process_learning()` pipeline

## Commands

### Backend
```bash
# Initialize database (creates DB, enables pgvector/uuid-ossp, creates tables)
python scripts/init_db.py

# Run dev server (port 8000)
uvicorn app.main:app --reload

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

## Environment Variables

Configured via `.env` in project root (loaded by pydantic-settings):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL async connection string (`postgresql+asyncpg://...`) |
| `OLLAMA_URL` | Ollama embedding server (default: `http://localhost:11434`) |
| `ANTHROPIC_API_KEY` | API key for LLM provider |
| `ANTHROPIC_BASE_URL` | Optional custom endpoint for Anthropic-compatible API |

## Prerequisites

- PostgreSQL with pgvector extension
- Ollama running locally with `nomic-embed-text` model pulled
- Python 3.9+ with dependencies from `requirements.txt`
- Node.js for the frontend

## Frontend Note

The frontend uses a custom Next.js version with breaking changes. Always read `node_modules/next/dist/docs/` before modifying Next.js-specific code (see `frontend/AGENTS.md`).
