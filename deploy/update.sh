#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  DisciplineOS — deploy a new release on the server.
#
#    deploy/update.sh [IMAGE_TAG]
#
#  Called by the GitHub Actions deploy job, and usable by hand. Pulls the
#  requested tag (default: latest), recreates the containers and waits for the
#  health endpoint. On failure the previous images are restored — a broken
#  release never stays on the box.
#
#  Requires: docker compose plugin, a filled .env next to this repo, curl.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."
TAG="${1:-latest}"
API_IMAGE_BASE="$(grep -oP '^API_IMAGE=\K[^:]+' .env || true)"
WEB_IMAGE_BASE="$(grep -oP '^WEB_IMAGE=\K[^:]+' .env || true)"
HEALTH_URL="${HEALTH_URL:-https://$(grep -oP '^DOMAIN=\K.*' .env)/api/v1/meta/health}"

say() { printf '\n\033[36m▸ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[[ -n "$API_IMAGE_BASE" ]] || fail "API_IMAGE is not set in .env"

# Remember what is running right now so we can roll back.
PREVIOUS_API="$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q api 2>/dev/null)" 2>/dev/null || true)"
PREVIOUS_WEB="$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q caddy 2>/dev/null)" 2>/dev/null || true)"

rollback() {
  printf '\033[33m! rolling back to %s / %s\033[0m\n' "${PREVIOUS_API:-none}" "${PREVIOUS_WEB:-none}"
  [[ -n "$PREVIOUS_API" ]] && sed -i "s|^API_IMAGE=.*|API_IMAGE=${PREVIOUS_API}|" .env
  [[ -n "$PREVIOUS_WEB" ]] && sed -i "s|^WEB_IMAGE=.*|WEB_IMAGE=${PREVIOUS_WEB}|" .env
  docker compose up -d --remove-orphans || true
}

wait_healthy() {
  local tries="${1:-45}"
  for _ in $(seq 1 "$tries"); do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  return 1
}

say "Deploying tag ${TAG}"
sed -i "s|^API_IMAGE=.*|API_IMAGE=${API_IMAGE_BASE}:${TAG}|; s|^WEB_IMAGE=.*|WEB_IMAGE=${WEB_IMAGE_BASE}:${TAG}|" .env

# Keep the local checkout in sync (migrations/Caddyfile/config live there).
git fetch --all --prune >/dev/null 2>&1 || true
git checkout --quiet "${BRANCH:-main}" 2>/dev/null || true
git pull --ff-only --quiet 2>/dev/null || true

say "Pulling images"
docker compose pull --quiet || { rollback; fail "image pull failed"; }

say "Recreating containers (migrations run on api start)"
docker compose up -d --remove-orphans || { rollback; fail "compose up failed"; }

say "Waiting for ${HEALTH_URL}"
if ! wait_healthy 45; then
  docker compose logs --tail 80 api || true
  rollback
  docker compose up -d --remove-orphans || true
  fail "the new release did not become healthy"
fi

# A dangling-image sweep keeps the VPS from filling up over months of deploys.
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true

say "Deployed ${TAG} successfully"
curl -fsS "$HEALTH_URL"; echo
