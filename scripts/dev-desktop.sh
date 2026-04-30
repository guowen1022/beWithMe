#!/usr/bin/env bash
# Dev launcher for beWithMe desktop (Electron shell).
# Starts: FastAPI backend, Next.js dev server, Electron shell.
# Kills children on exit.

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
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "$SKIP_BACKEND" != "1" ]]; then
  echo "[dev-desktop] starting backend: $BACKEND_CMD"
  (cd "$ROOT" && exec $BACKEND_CMD) &
  pids+=($!)
fi

echo "[dev-desktop] starting Next.js dev server on :$FRONTEND_PORT"
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
