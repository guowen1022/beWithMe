#!/usr/bin/env bash
# Stop the dev-services sidecars cleanly.
#
# Source of truth is the *port*, not the process name — ports are stable
# across launch methods (uvicorn vs `python -m`, with/without the venv,
# spawned by dev-services.sh vs dev-desktop.sh). Whoever is listening on
# a sidecar's port gets SIGTERM, then SIGKILL after GRACE seconds.
#
# Usage:
#   ./scripts/dev-services-stop.sh                   # stop sidecars only
#   ./scripts/dev-services-stop.sh --with-frontend   # also stop Next.js
#   ./scripts/dev-services-stop.sh --with-desktop    # also stop Electron(s)
#   ./scripts/dev-services-stop.sh --all             # sidecars + frontend + Electron
#   BASE_PORT=9000 ./scripts/dev-services-stop.sh    # match a relocated topology
#
# Knobs:
#   BASE_PORT      defaults 8000 (sidecars use BASE_PORT..BASE_PORT+5)
#   FRONTEND_PORT  defaults 3000
#   GRACE          defaults 5 seconds before SIGKILL

set -uo pipefail

BASE_PORT=${BASE_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-3000}
GRACE=${GRACE:-5}

WITH_FRONTEND=0
WITH_DESKTOP=0
for arg in "$@"; do
  case "$arg" in
    --with-frontend) WITH_FRONTEND=1 ;;
    --with-desktop)  WITH_DESKTOP=1 ;;
    --all) WITH_FRONTEND=1; WITH_DESKTOP=1 ;;
    -h|--help)
      sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "[dev-services-stop] unknown arg: $arg" >&2; exit 2 ;;
  esac
done

ports=()
for offset in 0 1 2 3 4 5; do ports+=($((BASE_PORT + offset))); done
[[ $WITH_FRONTEND -eq 1 ]] && ports+=("$FRONTEND_PORT")

pids_on_port() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u
}

# Collect every listening PID into a flat newline-separated list, then
# dedupe via sort -u. Avoids `declare -A` (bash 4+, missing on macOS's
# bundled bash 3.2).
raw_pids=""
for p in "${ports[@]}"; do
  port_pids=$(pids_on_port "$p")
  [[ -n "$port_pids" ]] && raw_pids="$raw_pids
$port_pids"
done
all_pids=$(printf '%s\n' "$raw_pids" | sed '/^$/d' | sort -u)

if [[ -z "$all_pids" ]]; then
  echo "[dev-services-stop] nothing listening on ports: ${ports[*]}"
  exit 0
fi

count=$(printf '%s\n' "$all_pids" | wc -l | tr -d ' ')
echo "[dev-services-stop] SIGTERM to $count pid(s) on ports ${ports[*]}: $(printf '%s\n' $all_pids | xargs)"
while read -r pid; do
  kill -TERM "$pid" 2>/dev/null || true
done <<< "$all_pids"

# Wait up to GRACE for graceful exit. uvicorn's lifespan + asyncio teardown
# usually completes in <1s; SSE keepalive loops can take a beat longer.
deadline=$((SECONDS + GRACE))
while [[ $SECONDS -lt $deadline ]]; do
  alive=0
  while read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then alive=$((alive + 1)); fi
  done <<< "$all_pids"
  if [[ $alive -eq 0 ]]; then
    echo "[dev-services-stop] all stopped cleanly"
    exit 0
  fi
  sleep 0.5
done

# Anything still alive gets SIGKILL.
remaining=""
while read -r pid; do
  if kill -0 "$pid" 2>/dev/null; then remaining="$remaining $pid"; fi
done <<< "$all_pids"
if [[ -n "$remaining" ]]; then
  echo "[dev-services-stop] SIGKILL stragglers:$remaining"
  for pid in $remaining; do
    kill -KILL "$pid" 2>/dev/null || true
  done
fi

if [[ $WITH_DESKTOP -eq 1 ]]; then
  # Kill any bewithme Electron parent. The pgrep pattern matches the
  # `node .../node_modules/.bin/electron .` launcher; killing it tears
  # down the entire Electron app + helpers.
  electron_pids=$(pgrep -f "node_modules/.bin/electron" 2>/dev/null || true)
  if [[ -n "$electron_pids" ]]; then
    echo "[dev-services-stop] SIGTERM Electron pids: $(echo $electron_pids | xargs)"
    for pid in $electron_pids; do kill -TERM "$pid" 2>/dev/null || true; done
    # Give the helpers a moment to wind down, then SIGKILL anything that's
    # still around (Electron's helper subtree can be stubborn).
    sleep 1
    pkill -KILL -f "node_modules/.bin/electron" 2>/dev/null || true
    pkill -KILL -f "node_modules/electron/dist" 2>/dev/null || true
  else
    echo "[dev-services-stop] no Electron processes running"
  fi
fi

echo "[dev-services-stop] done"
