#!/usr/bin/env bash
# Dev launcher for beWithMe. Starts:
#   - 6 backend sidecars (FastAPI/uvicorn)
#   - Next.js renderer on :3000 (Electron loads from this)
#   - Electron desktop shell — the only UI for this app
#
# There is no standalone web app. Next.js runs because Electron's renderer
# fetches from it; you don't open localhost:3000 in a browser.
#
# Ctrl-C cleans up everything (cleanup trap calls dev-services-stop.sh
# --all so stragglers don't survive).
#
# Usage:
#   ./scripts/dev-desktop.sh
#   SKIP_BACKEND=1 ./scripts/dev-desktop.sh   # skip backend startup

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_CMD=${BACKEND_CMD:-"$ROOT/scripts/dev-services.sh"}
FRONTEND_PORT=${FRONTEND_PORT:-3000}
SKIP_BACKEND=${SKIP_BACKEND:-0}

pids=()
cleanup() {
  trap - EXIT INT TERM
  echo
  echo "[dev-desktop] shutting down..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  # Belt-and-suspenders: stragglers (Electron helpers, uvicorn reloader
  # children) sometimes outlive their parent. Kill anything still on our
  # ports + any orphan Electron.
  "$ROOT/scripts/dev-services-stop.sh" --all >/dev/null 2>&1 || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Idempotent restart: sweep anything still listening on our ports from a
# previous run before starting fresh. Re-running this script (or running
# it after Ctrl-C left orphans) reliably picks up source changes —
# Python module-level state (e.g. workshop/canvas/tools/*.py block
# templates) only updates via process restart.
echo "[dev-desktop] sweeping any prior services on $FRONTEND_PORT + sidecar ports..."
FRONTEND_PORT=$FRONTEND_PORT "$ROOT/scripts/dev-services-stop.sh" --all >/dev/null 2>&1 || true

if [[ "$SKIP_BACKEND" != "1" ]]; then
  echo "[dev-desktop] starting backend: $BACKEND_CMD"
  (cd "$ROOT" && exec $BACKEND_CMD) &
  pids+=($!)
fi

echo "[dev-desktop] starting Next.js renderer on :$FRONTEND_PORT"
(cd "$ROOT/frontend" && PORT=$FRONTEND_PORT exec npm run dev) &
pids+=($!)

echo "[dev-desktop] waiting for Next.js to accept connections..."
for i in $(seq 1 60); do
  if curl -so /dev/null "http://localhost:$FRONTEND_PORT/"; then
    echo "[dev-desktop] Next.js is ready"
    break
  fi
  sleep 1
  if [[ $i -eq 60 ]]; then
    echo "[dev-desktop] timed out waiting for Next.js" >&2
    exit 1
  fi
done

echo "[dev-desktop] building desktop TS and launching Electron"
(cd "$ROOT/desktop" && npm run build >/dev/null)
(cd "$ROOT/desktop" && SHELL_URL="http://localhost:$FRONTEND_PORT/" npm run dev) &
pids+=($!)

wait
