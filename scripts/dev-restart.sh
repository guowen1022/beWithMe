#!/usr/bin/env bash
# Stop everything (sidecars + Next.js + Electron), then bring it all back.
# There's only one mode — backend + Next.js + Electron together.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[dev-restart] stopping everything…"
"$ROOT/scripts/dev-services-stop.sh" --all

echo "[dev-restart] starting fresh…"
exec "$ROOT/scripts/dev-desktop.sh"
