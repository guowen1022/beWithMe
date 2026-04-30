# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

beWithMe is a personalized reading assistant that uses episodic memory and a knowledge graph to tailor explanations to the user's background. Users paste passages, ask questions, and get answers informed by their profile, learning preferences, past interactions, and evolving concept mastery.

## Architecture

**Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) before any structural change** (new module, new sidecar, new dependency, new persona). It is the source of truth for the layer model (infra → silicon_brain → persona → tools → services), the dep-graph rules, the auth model, the persona/tool/sandbox vision, the migration trajectory, and the verification commands.

This file (`CLAUDE.md`) is the **operator's manual**: how to run, where logs live, env-var table, command reference. Architecture stays in `ARCHITECTURE.md`.

### One-paragraph summary

beWithMe is a multi-persona, LLM-driven assistant. **Personas** (today: teacher; planned: helper, engineer) decide what to do; **tools** (planned wrappers around services) are the verbs they invoke; **services** (six sidecars under `services/`) execute; **silicon_brain** persists state; **infra** is the stateless shared foundation. The frontend's only public face is the **shell** sidecar at port `BASE_PORT` (default 8000), which auth-gates and proxies to the others. Persona never imports silicon_brain at runtime — communication is HTTP via `SiliconBrainClient` and DTOs in `infra/contracts/`. Full details, dependency rules, and migration trajectory in [`ARCHITECTURE.md`](./ARCHITECTURE.md).

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
