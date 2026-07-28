#!/usr/bin/env bash
# Deploy a tag onto this ECS instance. Runs ON the box, not in CI.
#
# The CD pipeline invokes it through Alibaba Cloud Assistant (no inbound SSH),
# but it is an ordinary script — run it by hand to deploy or roll back:
#
#   ./scripts/deploy-ecs.sh sha-a1b2c3d4e5f6      # deploy a tag
#   ./scripts/deploy-ecs.sh --rollback            # back to the previous tag
#   ./scripts/deploy-ecs.sh --current             # what is running now
#
# Expects, in the deploy directory (default /opt/bewithme):
#   .env              provider keys, DATABASE_URL, auth mode/keys
#   .deploy-env       ACR coordinates (written once by the setup in DEPLOY.md)
#
# Health-gated: if the shell does not come up, it restores the previous tag
# automatically rather than leaving the box on a broken release.

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/bewithme}"
STATE_DIR="$DEPLOY_DIR/.deploy-state"
CURRENT_FILE="$STATE_DIR/current"
PREVIOUS_FILE="$STATE_DIR/previous"

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

cd "$DEPLOY_DIR" || die "deploy dir $DEPLOY_DIR not found"
mkdir -p "$STATE_DIR"

# ACR coordinates: registry, namespace, repo names.
# shellcheck disable=SC1091
[[ -f .deploy-env ]] && source .deploy-env
: "${ACR_REGISTRY:?set ACR_REGISTRY in $DEPLOY_DIR/.deploy-env}"
: "${ACR_NAMESPACE:?set ACR_NAMESPACE in $DEPLOY_DIR/.deploy-env}"

image_core() { echo "${ACR_REGISTRY}/${ACR_NAMESPACE}/bewithme-core:$1"; }
image_media() { echo "${ACR_REGISTRY}/${ACR_NAMESPACE}/bewithme-media:$1"; }

health_ok() {
  local i
  for ((i = 1; i <= HEALTH_RETRIES; i++)); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
      log "health OK after ${i} attempt(s)"
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
  done
  return 1
}

bring_up() {
  local tag=$1
  export IMAGE_CORE
  export IMAGE_MEDIA
  IMAGE_CORE="$(image_core "$tag")"
  IMAGE_MEDIA="$(image_media "$tag")"

  log "pulling $tag"
  "${COMPOSE[@]}" pull --quiet

  log "starting $tag"
  # --remove-orphans so a sidecar deleted from the compose file actually goes
  # away instead of lingering with stale code.
  "${COMPOSE[@]}" up -d --remove-orphans
}

case "${1:-}" in
  --current)
    cat "$CURRENT_FILE" 2>/dev/null || echo "(nothing deployed yet)"
    exit 0
    ;;
  --rollback)
    [[ -f $PREVIOUS_FILE ]] || die "no previous tag recorded — nothing to roll back to"
    TAG="$(cat "$PREVIOUS_FILE")"
    log "rolling back to $TAG"
    ;;
  "")
    die "usage: $0 <image-tag> | --rollback | --current"
    ;;
  *)
    TAG="$1"
    ;;
esac

PRIOR="$(cat "$CURRENT_FILE" 2>/dev/null || true)"

bring_up "$TAG"

if health_ok; then
  # Record only after the release proves itself, so `previous` always points
  # at a tag that was known good.
  [[ -n $PRIOR && $PRIOR != "$TAG" ]] && echo "$PRIOR" > "$PREVIOUS_FILE"
  echo "$TAG" > "$CURRENT_FILE"
  log "deployed $TAG"

  # Reclaim disk from superseded image layers. The media image is multi-GB and
  # an ECS system disk fills quickly across a few releases.
  docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true
  exit 0
fi

log "health check FAILED for $TAG"
"${COMPOSE[@]}" logs --tail 50 shell || true

if [[ -n $PRIOR && $PRIOR != "$TAG" ]]; then
  log "restoring previous tag $PRIOR"
  bring_up "$PRIOR"
  if health_ok; then
    log "restored $PRIOR — deployment of $TAG aborted"
  else
    log "restore of $PRIOR ALSO failed — manual intervention required"
  fi
fi

exit 1
