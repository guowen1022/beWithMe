#!/usr/bin/env bash
# Launch all 6 beWithMe sidecars from a single BASE_PORT.
#   shell      → BASE_PORT       (default 8000)
#   persona    → BASE_PORT + 1   (the teacher; agent-driven endpoints)
#   knowledge  → BASE_PORT + 2
#   transcribe → BASE_PORT + 3
#   speak      → BASE_PORT + 4
#   browser    → BASE_PORT + 5
#
# Usage:
#   ./scripts/dev-services.sh                    # uses BASE_PORT=8000
#   BASE_PORT=9000 ./scripts/dev-services.sh     # whole topology slides to 9000-9005

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE_PORT=${BASE_PORT:-8000}
RELOAD=${RELOAD:-1}

# Export so each child sidecar (and the shell's proxy URL builder) sees it.
export BASE_PORT

# Resolve `uvicorn` from the venv if `uvicorn` isn't on PATH (the venv often
# isn't activated when this script is run from the project root).
if command -v uvicorn >/dev/null 2>&1; then
  UVICORN=uvicorn
elif [[ -x "$ROOT/.venv/bin/uvicorn" ]]; then
  UVICORN="$ROOT/.venv/bin/uvicorn"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  UVICORN="$ROOT/.venv/bin/python -m uvicorn"
else
  echo "[dev-services] no uvicorn found (PATH or .venv)" >&2
  exit 1
fi

reload_flag=""
if [[ "$RELOAD" == "1" ]]; then
  # Scope --reload to the Python source dirs ONLY. Default uvicorn behavior
  # watches the whole CWD, which means every sidecar polls .venv/,
  # frontend/node_modules/, data/sessions/, etc. — six watchers chewing 75%+
  # CPU each. Restricting to the dirs that actually contain runtime code
  # drops idle CPU near zero.
  reload_flag="--reload --reload-dir services --reload-dir persona --reload-dir silicon_brain --reload-dir infra --reload-dir tools --reload-dir agents --reload-dir workshop"
fi

pids=()
cleanup() {
  trap - EXIT INT TERM
  echo
  echo "[dev-services] shutting down..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start() {
  local name=$1 offset=$2 module=$3
  local port=$((BASE_PORT + offset))
  echo "[dev-services] starting $name on :$port"
  $UVICORN "$module" --host 0.0.0.0 --port "$port" $reload_flag &
  pids+=($!)
}

# Order matters loosely: bring up sidecars before the shell so the first
# proxied request finds something on the other end.
start knowledge  2 services.knowledge.main:app
start persona    1 services.persona.main:app
start transcribe 3 services.transcribe.main:app
start speak      4 services.speak.main:app
start browser    5 services.browser.main:app
start shell      0 services.shell.main:app

wait
