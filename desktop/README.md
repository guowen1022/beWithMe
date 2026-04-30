# beWithMe Desktop

Electron wraps the existing web UI in a native Mac window. No desktop-specific UI — the Mac app loads the same `/` route as `http://localhost:3000/`.

## Prerequisites

- Node.js 20+
- Local Postgres (with pgvector) and Ollama — see the top-level `README.md`.

## Dev

From the repo root:

```bash
./scripts/dev-desktop.sh
```

Starts the FastAPI backend, the Next.js dev server, and the Electron window. `Ctrl+C` tears everything down.

If the backend is already running:

```bash
SKIP_BACKEND=1 ./scripts/dev-desktop.sh
```
