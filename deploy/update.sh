#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  DisciplineOS — deploy a new release on the server.
#
#    deploy/update.sh [IMAGE_TAG]
#
#  Called by the GitHub Actions deploy job, and usable by hand. The images come from
#  one of two places — BUILD_LOCAL=1 / 0 (in the environment or in .env) forces one,
#  otherwise it follows API_IMAGE in .env: a registry or owner in the name
#  (ghcr.io/owner/disciplineos-api:latest) is pulled, a bare local name
#  (disciplineos-api:local) is built here.
#
#    pull   IMAGE_TAG (default: latest) is pulled from the registry.
#    build  the checked-out commit is built on this server and tagged sha-<12 hex of
#           the commit> (-dirty with uncommitted changes); the web bundle carries the same
#           label as VITE_APP_VERSION. With an IMAGE_TAG nothing is built: the images built
#           earlier under that tag are used again — a rollback to a release still on the box.
#
#  Then the containers are recreated and the health endpoint is awaited. On failure the
#  previous images are restored — a broken release never stays on the box.
#
#  Requires: docker compose plugin (and buildx for builds), a filled .env next to this
#  repo, curl.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."
TAG_ARG="${1:-}"

say() { printf '\n\033[36m▸ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

env_get() { # env_get KEY → the last KEY=value line of .env (value only; empty when absent)
  { grep -oP "^$1=\K.*" .env 2>/dev/null || true; } | tail -n 1
}
image_base() { sed -E 's/:[^:/]*$//' <<<"$1"; } # the name without its tag (a registry port is not a tag)

API_IMAGE_BASE="$(image_base "$(env_get API_IMAGE)")"
WEB_IMAGE_BASE="$(image_base "$(env_get WEB_IMAGE)")"
HEALTH_URL="${HEALTH_URL:-https://$(env_get DOMAIN)/api/v1/meta/health}"

[[ -n "$API_IMAGE_BASE" ]] || fail "API_IMAGE is not set in .env"
[[ -n "$WEB_IMAGE_BASE" ]] || fail "WEB_IMAGE is not set in .env"

BUILD_LOCAL="${BUILD_LOCAL:-$(env_get BUILD_LOCAL)}"
if [[ -z "$BUILD_LOCAL" ]]; then
  case "$(env_get API_IMAGE)" in
    */*) BUILD_LOCAL=0 ;;
    *) BUILD_LOCAL=1 ;;
  esac
fi

# Remember what is running right now so we can roll back.
PREVIOUS_API="$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q api 2>/dev/null)" 2>/dev/null || true)"
PREVIOUS_WEB="$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q caddy 2>/dev/null)" 2>/dev/null || true)"

set_images() { # set_images API_IMAGE WEB_IMAGE → .env
  sed -i "s|^API_IMAGE=.*|API_IMAGE=${1}|; s|^WEB_IMAGE=.*|WEB_IMAGE=${2}|" .env
}

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

# Builds pile up on this box (two images per commit): keep the newest KEEP_LOCAL_BUILDS
# (default 3) of each — the running release and the ones to roll back to. An image a
# container still uses cannot be removed and stays.
prune_local_builds() {
  local keep="${KEEP_LOCAL_BUILDS:-3}" repo tag
  for repo in "$API_IMAGE_BASE" "$WEB_IMAGE_BASE"; do
    while IFS= read -r tag; do
      docker image rm "${repo}:${tag}" >/dev/null 2>&1 || true
    done < <(docker image ls --format '{{.Tag}}' "$repo" 2>/dev/null | grep '^sha-' | tail -n "+$((keep + 1))" || true)
  done
}

# Keep the local checkout in sync (migrations/Caddyfile/config live there — and, when the
# images are built here, the code itself).
git fetch --all --prune >/dev/null 2>&1 || warn "git fetch failed — deploying the checkout as it is"
git checkout --quiet "${BRANCH:-main}" 2>/dev/null || true
git pull --ff-only --quiet 2>/dev/null || warn "git pull --ff-only failed — deploying the checkout as it is"

if [[ "$BUILD_LOCAL" == "1" ]]; then
  # the same label CI gives its images: sha-<12 hex of the commit>
  VERSION="sha-$(git rev-parse --short=12 HEAD)"
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then VERSION="${VERSION}-dirty"; fi
  TAG="${TAG_ARG:-$VERSION}"
else
  TAG="${TAG_ARG:-latest}"
fi
NEW_API="${API_IMAGE_BASE}:${TAG}"
NEW_WEB="${WEB_IMAGE_BASE}:${TAG}"

say "Deploying tag ${TAG}"
if [[ "$BUILD_LOCAL" == "1" ]]; then
  if [[ -n "$TAG_ARG" ]]; then
    docker image inspect "$NEW_API" "$NEW_WEB" >/dev/null 2>&1 \
      || fail "no local images tagged ${TAG} (${NEW_API}, ${NEW_WEB}) — run deploy/update.sh without a tag to build the checkout"
    say "Using the images built earlier as ${TAG}"
  else
    docker buildx version >/dev/null 2>&1 \
      || fail "docker buildx is missing — BuildKit, which 'docker compose build' uses, needs it (Ubuntu's docker.io: apt-get install docker-buildx)"
    say "Building ${NEW_API} and ${NEW_WEB} from commit $(git rev-parse --short HEAD) — the running release is untouched until they are ready"
    API_IMAGE="$NEW_API" WEB_IMAGE="$NEW_WEB" VITE_APP_VERSION="$VERSION" docker compose build \
      || fail "the image build failed — nothing was changed"
  fi
  set_images "$NEW_API" "$NEW_WEB"
  docker compose pull --quiet postgres || warn "could not pull the postgres image — using the one on this host"
else
  set_images "$NEW_API" "$NEW_WEB"
  say "Pulling images"
  docker compose pull --quiet || { rollback; fail "image pull failed"; }
fi

say "Recreating containers (migrations run on api start)"
docker compose up -d --remove-orphans || { rollback; fail "compose up failed"; }

say "Waiting for ${HEALTH_URL}"
if ! wait_healthy 45; then
  docker compose logs --tail 80 api || true
  rollback
  fail "the new release did not become healthy"
fi

# A dangling-image sweep keeps the VPS from filling up over months of deploys.
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true
if [[ "$BUILD_LOCAL" == "1" ]]; then
  prune_local_builds
  docker builder prune -f --filter "until=168h" >/dev/null 2>&1 || true
fi

say "Deployed ${TAG} successfully"
curl -fsS "$HEALTH_URL"; echo
