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

### Module architecture

The backend has two self-contained domain modules. They do **not** cross-reference each other. `ask.py` assembles from both.

**`app/user_profile/`** — Static user preferences (who the user is, how they like to learn):
- `models.py` — `LearningPreferences` table: categorical labels (explanation_style, depth, pacing, etc.) + a 768-dim `preference_embedding` vector
- `ema.py` — Exponential Moving Average math for the preference embedding. Updated after every interaction by blending the interaction embedding into the running average (`alpha=0.15`)
- `preference_distiller.py` — LLM-based distillation: analyzes last 20 interactions to produce categorical preference labels. Auto-triggers every 10 interactions
- `state.py` — `UserProfileState` dataclass + `get_user_profile(db, session_id)` assembler + `boost_query_embedding(db, query)` for preference-weighted retrieval

**`app/knowledge/`** — Dynamic learning state (what the user knows, how it connects):
- `models.py` — `ConceptNode` (name, state, half_life, encounter_count) + `ConceptEdge` (source, target, weight, edge_type)
- `hlr.py` — Half-Life Regression (Duolingo-inspired): mastery decays as `2^(-hours/half_life)`. States: solid > learning > rusty > faded
- `concepts.py` — Regex-based concept extraction from `CONCEPTS:` line in model answers + HLR-aware upsert
- `edges.py` — Temporal edges between co-occurring concepts, with weight decay
- `graph.py` — NetworkX graph: loads from DB, walks neighborhoods, produces prompt-ready context
- `visualize.py` — Graph data export for the frontend debug panel

### Key data flow

1. User submits a question via `POST /api/ask/stream` (SSE)
2. `ask.py` assembles context from both modules:
   - `get_user_profile(db, session_id)` → preferences + session signals
   - `boost_query_embedding(db, query)` → preference-weighted retrieval vector
   - `get_concepts(db)` → concept mastery snapshot
   - `get_graph_context(db, names)` → related concept neighborhood
3. `prompt_builder.py` combines user profile, concepts, graph context, similar past interactions, and document chunks into the LLM prompt
4. LLM generates a streaming response (with `CONCEPTS:` line at the end)
5. Background task (`post_interaction.py`) runs after each answer:
   - Embeds the interaction via Ollama
   - EMA-updates the preference embedding (vector math, no LLM call)
   - Parses concepts from the answer → upserts nodes with HLR → creates temporal edges
   - Auto-distills categorical preferences if ≥10 new interactions

### Other important patterns

- **Singleton LLM client**: `app/services/llm.py` uses a module-level client; `anthropic_base_url` in config redirects to a proxy/alternative provider
- **Vector retrieval**: `app/services/retrieval.py` searches interactions and document chunks by cosine similarity via pgvector. Queries are boosted with the user's preference embedding (70% query, 30% preference)
- **Document chunking**: 500-word chunks with 50-word overlap, embedded in background tasks
- **`app/db_base.py`**: Shared SQLAlchemy `Base` class, extracted to avoid circular imports between modules
- **Re-export stubs**: `app/models/preferences.py` and `app/models/concept.py` re-export from `user_profile` and `knowledge` respectively, preserving backward-compatible import paths

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
