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
End-to-end integration benchmarks in `benchmark/`. Two LLM-behavior buckets are organized
by sub-package; each region/topic owns its own `questions.yaml` and a co-located `runs/`
folder where every round drops `results.json` (full answer text), `metadata.json` (git sha,
env, full system prompt per interaction, profile snapshot), and `comments.md` (free-form
human or LLM-judge annotations). Whatever `LLM_PROVIDER` is active in `.env` is what the
benchmark exercises — switch providers by changing the env var and restarting the backend.
```bash
# Backend must be running (uvicorn) on :8000.

# Reading-Q&A behavior — one region per knowledge area.
python -m benchmark.model_behavior --list                       # list runnable regions
python -m benchmark.model_behavior --region biology --reset
python -m benchmark.model_behavior --region computer_science

# Goal-planning behavior — one slug per goal topic.
python -m benchmark.goal_planning --list                        # list runnable topics
python -m benchmark.goal_planning --topic learn-web-dev --reset
python -m benchmark.goal_planning --topic deploy-ml-production

# File-attachment focused module test — exercises the PDF / media
# upload pipeline in isolation, one slug per fixture.
python -m benchmark.file_understanding --list                   # list runnable slugs
python -m benchmark.file_understanding --slug gettysburg-pdf --reset
```

**File attachments are a cross-cutting capability.** Any `model_behavior`
session can carry a `file:` block in place of an inline `passage:`:

```yaml
sessions:
  - title: "Attention Is All You Need — read this paper"
    file:
      kind: pdf
      text_source: |              # runner materializes a PDF at runtime
        ...full paper text...
      # OR: path: "fixtures/attention.pdf"   # an existing file on disk
    interactions:
      - selected_text: "scaled dot-product attention"
        question: "Why divide by sqrt(d_k)?"
```

When a session declares a file, the runner uploads it before the session,
plugs the extracted text into `passage_text`, and passes the resulting
`document_id` on every ask so the Interaction row is linked back to the
document. For `file.kind: video|audio|image` the runner posts to
`/api/media/upload` and surfaces the server-side path in the passage so
the persona's `look_at_video` / `look_at_image` tools can find it.

`benchmark/file_understanding/` is then the **focused module test** for
the upload pipeline itself — one file, one question list, no
multi-session context. Use it when iterating on the upload/extraction
path; use a file-attached session inside a `model_behavior` region when
you want to test how the teacher handles a real reading-with-attachment
flow.

Add a new region/topic/slug by creating one of:
- `benchmark/model_behavior/<slug>/questions.yaml` — reading (`sessions:` block; any session may carry `file:`)
- `benchmark/goal_planning/<slug>/questions.yaml` — goals (`goal:` + `actions:`)
- `benchmark/file_understanding/<slug>/questions.yaml` — upload-pipeline test (`file:` + `questions:`)

See `benchmark/model_behavior/biology/questions.yaml` (reading) and
`benchmark/file_understanding/gettysburg-pdf/questions.yaml` (file) for
reference shapes. The CLI auto-discovers any slug whose YAML has the
right top-level key. Past runs live under `runs/` next to the YAML —
gitignored by default; commit individual rounds manually when you want
to keep them as a reference baseline.

The original `benchmark/scenarios.py` + `benchmark/runner.py` (`python -m benchmark --scenario N`)
still work but are deprecated; new question sets should go in the YAML layout above.

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
- `infra/config.py` — `OLLAMA_URL`, `EMBEDDING_*`, `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_*`, `DEEPSEEK_*`, `VISION_PROVIDER`, `DOUBAO_*`
- `services/transcribe/main.py` — `WHISPER_MODEL_PATH`, `WHISPER_THREADS`, `EOU_*`
- `services/speak/main.py` — `KOKORO_*`

`LLM_PROVIDER` and `VISION_PROVIDER` are independent. The main reasoning LLM stays
text-only (DeepSeek V4 today); vision calls are routed through
`infra.model.vision.describe_image` to whichever provider `VISION_PROVIDER` selects,
and the result flows back as plain text. Tools `look_at_image` and (planned) `web_view`
with `include_screenshot=true` are the entry points.

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
| `VISION_PROVIDER` | Active image-understanding backend (default: `doubao`). Dispatches to `infra/model/vision/<provider>.py`. Independent of `LLM_PROVIDER`. |
| `DOUBAO_API_KEY` | Volces Ark API key for Doubao. Required when `VISION_PROVIDER=doubao`. |
| `DOUBAO_BASE_URL` | Volces Ark endpoint (e.g. `https://ark.cn-beijing.volces.com/api/v3`). Required when `VISION_PROVIDER=doubao`. |
| `DOUBAO_VISION_MODEL` | Doubao model name (e.g. `doubao-seed-2-0-lite-260428`). Required when `VISION_PROVIDER=doubao`. |
| `EOU_MODEL_PATH` | Path to the LiveKit turn-detector ONNX file. Unset → `/api/eou` returns 503; ambient gates fail open (today's behavior, no disfluency handling). Fetch with `scripts/fetch_eou_model.sh`. |
| `EOU_TOKENIZER_PATH` | Path to the EOU tokenizer dir (containing `tokenizer.json`) or the file itself. Required when `EOU_MODEL_PATH` is set. |
| `EOU_THRESHOLD` | P(end-of-turn) above which the gate commits. Default `0.55` — lower = more eager commits, higher = absorbs more disfluencies. |
| `EOU_MAX_TOKENS` | Tail-truncate the EOU input to this many tokens. Default `256`. |
| `BEWITHME_DEBUG` | Master switch for developer debug surfaces: the Mirror canvas block (mounted by app_operator's `show_mirror`), the top-right teacher-thinking panel, and the desktop's detached Chromium DevTools window. Default `1` (on); set `0` to hide all three. `scripts/dev-desktop.sh` fans it out to the frontend as `NEXT_PUBLIC_BEWITHME_DEBUG` (inlined by `next dev` at start) and to Electron as `BEWITHME_DEBUG`. To launch clean: `BEWITHME_DEBUG=0 ./scripts/dev-desktop.sh`. |
| `no_proxy` | Comma-separated hosts that bypass `http_proxy`/`https_proxy`. Leading dot = subdomain match. e.g. `api.deepseek.com,.minimaxi.com,.volces.com`. |

## Prerequisites

- PostgreSQL with pgvector extension
- Ollama running locally with `nomic-embed-text` model pulled
- Python 3.9+ with dependencies from `requirements.txt`
- Node.js for the frontend
- `ffmpeg` (with `ffprobe`) on PATH — `brew install ffmpeg`. Required by `services/transcribe` and `infra/media` (video understanding).

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
