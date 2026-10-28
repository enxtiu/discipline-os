#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  DisciplineOS — one-shot bootstrap for a fresh Debian/Ubuntu VPS.
#
#    ssh root@your-server
#    curl -fsSL https://raw.githubusercontent.com/OWNER/discipline-os/main/deploy/install.sh -o install.sh
#    bash install.sh
#
#  Idempotent: safe to re-run. It will
#    1. install docker + the compose plugin + age (for encrypted backups)
#    2. configure the firewall (22/80/443)
#    3. clone the repo into $APP_DIR and write .env with generated secrets
#    4. pull the published images and start the stack
#    5. run migrations, then create the owner and print the first pairing code
#
#  Everything it cannot decide (domain, email, GHCR credentials, age key) is
#  asked interactively or read from an existing .env.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/disciplineos}"
REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-main}"
say() { printf '\n\033[36m▸ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

ask() { # ask VAR "prompt" [default]
  local var="$1" prompt="$2" default="${3:-}" answer
  if [[ -n "${!var:-}" ]]; then return 0; fi
  read -r -p "$prompt${default:+ [$default]}: " answer </dev/tty
  printf -v "$var" '%s' "${answer:-$default}"
}

[[ "$(id -u)" == "0" ]] || die "run as root (or via sudo)"

# --------------------------------------------------------------------------- #
# 1. packages
# --------------------------------------------------------------------------- #
say "Installing docker, compose plugin, age, git"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.gpg 2>/dev/null \
    || curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /tmp/docker.gpg
  gpg --dearmor -o /etc/apt/keyrings/docker.gpg /tmp/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin age git curl openssl >/dev/null
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version >/dev/null || die "the docker compose plugin is missing"

# --------------------------------------------------------------------------- #
# 2. firewall
# --------------------------------------------------------------------------- #
if command -v ufw >/dev/null 2>&1; then
  say "Configuring ufw (22, 80, 443)"
  ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  ufw allow 443/udp >/dev/null   # HTTP/3
  ufw --force enable >/dev/null
else
  warn "ufw is not installed — make sure only 22/80/443 are reachable"
fi

# --------------------------------------------------------------------------- #
# 3. code + .env
# --------------------------------------------------------------------------- #
if [[ ! -d "$APP_DIR/.git" ]]; then
  [[ -n "$REPO_URL" ]] || ask REPO_URL "Git clone URL of the repository"
  say "Cloning $REPO_URL → $APP_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  say "Repository already present — pulling $BRANCH"
  git -C "$APP_DIR" fetch --all --prune && git -C "$APP_DIR" checkout "$BRANCH" && git -C "$APP_DIR" pull --ff-only
fi

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  say "Writing .env (secrets are generated locally; nothing is echoed to CI)"
  ask DOMAIN "Public domain (A-record must point here)"
  ask ACME_EMAIL "E-mail for Let's Encrypt expiry notices (optional, Enter to skip)"
  ask GHCR_OWNER "GitHub owner (lowercase) that publishes the images"
  ask BACKUP_REPO "Private git repo for encrypted backups" ""

  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  SECRET_KEY="$(openssl rand -hex 32)"

  sed -e "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" \
      -e "s|^ACME_EMAIL=.*|ACME_EMAIL=${ACME_EMAIL}|" \
      -e "s|^API_IMAGE=.*|API_IMAGE=ghcr.io/${GHCR_OWNER}/disciplineos-api:latest|" \
      -e "s|^WEB_IMAGE=.*|WEB_IMAGE=ghcr.io/${GHCR_OWNER}/disciplineos-web:latest|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${POSTGRES_PASSWORD}|" \
      -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://dsos:${POSTGRES_PASSWORD}@postgres:5432/dsos|" \
      -e "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET_KEY}|" \
      -e "s|^BACKUP_REPO=.*|BACKUP_REPO=${BACKUP_REPO}|" \
      .env.example > .env
  chmod 600 .env

  if [[ -n "$BACKUP_REPO" ]]; then
    say "Generating the age key pair for backups"
    mkdir -p /root/.config/age
    age-keygen -o /root/.config/age/dsos.key 2>/dev/null || age-keygen -o /root/.config/age/dsos.key
    chmod 600 /root/.config/age/dsos.key
    AGE_RECIPIENT="$(grep -oE 'age1[0-9a-z]+' /root/.config/age/dsos.key | head -1)"
    sed -i "s|^AGE_RECIPIENT=.*|AGE_RECIPIENT=${AGE_RECIPIENT}|" .env
    echo
    warn "BACK THIS KEY UP NOW — without it every dump is unrecoverable:"
    echo "    /root/.config/age/dsos.key"
    echo "  Store it in your password manager, not on the server alone."
  fi
else
  say ".env already exists — leaving it untouched"
fi

# --------------------------------------------------------------------------- #
# 4. registry login + start
# --------------------------------------------------------------------------- #
GHCR_USER="$(grep -oP '^API_IMAGE=ghcr\.io/\K[^/]+' .env || true)"
if [[ -n "${GHCR_TOKEN:-}" && -n "$GHCR_USER" ]]; then
  say "Logging in to ghcr.io as $GHCR_USER"
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
else
  warn "GHCR_TOKEN is not set: if the images are private, run"
  warn "  echo <PAT> | docker login ghcr.io -u ${GHCR_USER:-OWNER} --password-stdin"
fi

say "Pulling images and starting the stack"
docker compose pull --quiet
docker compose up -d --remove-orphans

say "Waiting for the API to become healthy"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1/api/v1/meta/health" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS "http://127.0.0.1/api/v1/meta/health" || warn "the API is not answering yet — check: docker compose logs api"

# --------------------------------------------------------------------------- #
# 5. owner + first pairing code
# --------------------------------------------------------------------------- #
say "Creating the owner account (idempotent)"
docker compose exec -T api python -m app.cli create-owner || true

cat <<EOF

  ✔ DisciplineOS is up at https://$(grep -oP '^DOMAIN=\K.*' .env)

  Next steps
    • Install the PWA: open the URL in Safari on the iPhone → Share → Add to Home Screen
    • Pair this device with the code printed above
    • Nightly backups: ${BACKUP_REPO:-<not configured>}
      add the SSH deploy key to that repo: ssh-keygen -t ed25519 -f /root/.ssh/dsos-backup -N ""
    • Restore drill: deploy/backup/restore.sh <file.sql.age>

  Handy
    make logs      docker compose logs -f api
    make pair      print a fresh pairing code
    make backup    run an encrypted backup now
EOF
