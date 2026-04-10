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

### Key data flow

1. User submits a question about a passage via `POST /api/ask/stream` (SSE)
2. `prompt_builder.py` assembles context: user profile, learning preferences, similar past interactions (vector search), relevant document chunks, and known concepts
3. LLM generates a streaming response
4. Background tasks (`post_interaction.py`) embed the interaction and extract/update concept nodes

### Important patterns

- **Singleton LLM client**: `app/services/llm.py` uses a module-level client instance; `anthropic_base_url` in config redirects to a proxy/alternative provider
- **Vector retrieval**: `app/services/retrieval.py` searches both interactions and document chunks by cosine similarity using pgvector
- **Concept state machine**: Concepts progress through `new → learning → understood → mastered` based on encounter count in `concept_extractor.py`
- **Document chunking**: 500-word chunks with 50-word overlap, embedded in background tasks
- **Profile distillation**: `profile_distiller.py` analyzes past interactions to infer learning preferences (explanation style, depth, analogy affinity, etc.)

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
