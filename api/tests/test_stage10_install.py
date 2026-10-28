"""Stage 10: deploy/install.sh — Docker that is already on the box stays.

The scripts run for real against a throw-away git origin/clone, with fake ``docker``, ``apt-get``,
``apt-cache``, ``dpkg-query``… first on ``PATH``. The fake docker keeps its state (which images exist, which
release is running) in a directory, so a rollback really goes back to an image that is there.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.stage10_helpers import REPO, Sandbox, plain, run

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
}

# --------------------------------------------------------------------------- #
# The fakes
# --------------------------------------------------------------------------- #
FAKE_DOCKER = r"""
state="${FAKE_STATE:?}"
mkdir -p "$state/images"
log() { echo "docker $*" >> "$state/calls.log"; }
key() { printf '%s' "$1" | tr '/:' '__'; }
env_image() { { grep -oP "^$1=\K.*" .env 2>/dev/null || true; } | tail -n 1; }
mkimage() { printf '%s %s\n' "$(date +%s%N)" "$1" > "$state/images/$(key "$1")"; }
have_image() { [[ -f "$state/images/$(key "$1")" ]]; }
list_tags() { # newest first
  local f ts ref
  for f in "$state"/images/*; do
    [[ -f "$f" ]] || continue
    read -r ts ref < "$f"
    if [[ "$ref" == "$1":* ]]; then echo "$ts ${ref#"$1":}"; fi
  done | sort -rn | cut -d' ' -f2-
}

case "$1" in
  --version) echo "Docker version 26.1.3, build fake"; exit 0 ;;
  buildx) [[ "${2:-}" == version ]] && { [[ -z "${FAKE_NO_BUILDX:-}" || -f "$state/buildx-installed" ]]; exit $?; }; exit 1 ;;
  login) log "$*"; exit 0 ;;
  inspect)
    case "${*: -1}" in
      cid-api) cat "$state/running_api" 2>/dev/null || exit 1 ;;
      cid-caddy) cat "$state/running_caddy" 2>/dev/null || exit 1 ;;
      *) exit 1 ;;
    esac ;;
  image)
    case "$2" in
      inspect) shift 2; for ref in "$@"; do have_image "$ref" || exit 1; done ;;
      ls) list_tags "${*: -1}" ;;
      rm)
        log "$*"
        ref="$3"
        running="$(cat "$state/running_api" "$state/running_caddy" 2>/dev/null || true)"
        if grep -qxF "$ref" <<<"$running"; then echo "image is being used by running container" >&2; exit 1; fi
        rm -f "$state/images/$(key "$ref")" ;;
      prune) log "$*" ;;
    esac ;;
  builder) log "$*" ;;
  compose)
    shift
    case "$1" in
      version) [[ -z "${FAKE_NO_COMPOSE:-}" || -f "$state/compose-installed" ]]; exit $? ;;
      ps) [[ -f "$state/running_${3:-api}" ]] && echo "cid-${3:-api}"; exit 0 ;;
      build)
        api="${API_IMAGE:-$(env_image API_IMAGE)}"; web="${WEB_IMAGE:-$(env_image WEB_IMAGE)}"
        log "compose build API_IMAGE=$api WEB_IMAGE=$web VITE_APP_VERSION=${VITE_APP_VERSION:-}"
        [[ -z "${FAKE_BUILD_FAIL:-}" ]] || { echo "ERROR: the build failed" >&2; exit 1; }
        mkimage "$api"; mkimage "$web" ;;
      pull)
        log "compose $*"
        [[ -z "${FAKE_PULL_FAIL:-}" ]] || { echo "pull failed" >&2; exit 1; }
        if [[ "$*" != *postgres* ]]; then mkimage "$(env_image API_IMAGE)"; mkimage "$(env_image WEB_IMAGE)"; fi ;;
      up)
        api="$(env_image API_IMAGE)"; web="$(env_image WEB_IMAGE)"
        log "compose up API_IMAGE=$api WEB_IMAGE=$web"
        [[ -z "${FAKE_UP_FAIL:-}" ]] || exit 1
        for ref in "$api" "$web"; do
          if ! have_image "$ref"; then
            if [[ "$ref" == */* ]]; then mkimage "$ref"; else echo "pull access denied for $ref" >&2; exit 1; fi
          fi
        done
        printf '%s' "$api" > "$state/running_api"; printf '%s' "$web" > "$state/running_caddy" ;;
      *) log "compose $*" ;;
    esac ;;
  *) log "$*" ;;
esac
"""

# the API answers unless the release that runs now ends in $FAKE_UNHEALTHY_SUFFIX
FAKE_CURL = r"""
case "${*: -1}" in
  *meta/health)
    running="$(cat "$FAKE_STATE/running_api" 2>/dev/null || true)"
    if [[ -n "${FAKE_UNHEALTHY_SUFFIX:-}" && "$running" == *"$FAKE_UNHEALTHY_SUFFIX" ]]; then exit 22; fi
    echo '{"status":"ok","database":true}' ;;
esac
"""

# apt: what is installed (dpkg) and what has a candidate (apt-cache) comes from the environment
FAKE_APT_GET = r"""
echo "apt-get $*" >> "$FAKE_STATE/calls.log"
for pkg in "$@"; do  # installing a plugin package makes the plugin work
  case "$pkg" in
    docker-compose-v2 | docker-compose-plugin) touch "$FAKE_STATE/compose-installed" ;;
    docker-buildx | docker-buildx-plugin) touch "$FAKE_STATE/buildx-installed" ;;
  esac
done
"""
FAKE_APT_CACHE = r"""
if [[ "$1" == policy ]]; then
  for pkg in $FAKE_APT_AVAILABLE; do
    [[ "$pkg" == "$2" ]] && printf '%s:\n  Installed: (none)\n  Candidate: 1.0-fake\n' "$2"
  done
fi
exit 0
"""
FAKE_DPKG_QUERY = r"""
pkg="${*: -1}"
for installed in $FAKE_DPKG_INSTALLED; do
  [[ "$installed" == "$pkg" ]] && { printf 'install ok installed'; exit 0; }
done
printf 'unknown ok not-installed'; exit 1
"""


class Host(Sandbox):
    """A server: a git origin, the fakes, and (after ``install``) a checkout in ``app/``."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.state = root / "state"
        self.state.mkdir()
        self.origin = root / "origin"
        self.app = root / "app"
        self.fake("docker", FAKE_DOCKER)
        self.fake("curl", FAKE_CURL)
        self.fake("apt-get", FAKE_APT_GET)
        self.fake("apt-cache", FAKE_APT_CACHE)
        self.fake("dpkg-query", FAKE_DPKG_QUERY)
        self.fake("id", '[[ "$1" == -u ]] && echo 0 || /usr/bin/id "$@"\n')
        self.fake("systemctl", "exit 0\n")
        self.fake("sleep", "exit 0\n")
        self._make_origin()

    # ---- git -------------------------------------------------------------
    def git(self, *args: str, cwd: Path | None = None) -> str:
        done = run(["git", *args], cwd=cwd or self.origin, env={**self.environ(), **GIT_ENV})
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def _make_origin(self) -> None:
        self.origin.mkdir()
        for rel in (".env.example", "deploy/update.sh"):
            target = self.origin / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, target)
        (self.origin / "docker-compose.yml").write_text("services: {}\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first release")

    def release(self, name: str) -> str:
        """A new commit on the origin; returns its 12-hex id (the tag a local build will get)."""
        (self.origin / f"{name}.txt").write_text(name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "--short=12", "HEAD")

    def head(self, cwd: Path | None = None) -> str:
        return self.git("rev-parse", "--short=12", "HEAD", cwd=cwd or self.app)

    # ---- state -----------------------------------------------------------
    @property
    def calls(self) -> list[str]:
        log = self.state / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def calls_like(self, prefix: str) -> list[str]:
        return [c for c in self.calls if c.startswith(prefix)]

    def images(self) -> list[str]:
        refs = []
        for f in sorted((self.state / "images").glob("*")):
            refs.append(f.read_text().split(maxsplit=1)[1].strip())
        return sorted(refs)

    def add_image(self, ref: str) -> None:
        folder = self.state / "images"
        folder.mkdir(exist_ok=True)
        # unique, increasing timestamps: the newest image sorts first
        stamp = 1_000_000_000_000_000_000 + len(list(folder.glob("*"))) * 1_000_000_000
        (folder / ref.replace("/", "_").replace(":", "_")).write_text(f"{stamp} {ref}\n")

    def running(self, api: str, web: str) -> None:
        (self.state / "running_api").write_text(api)
        (self.state / "running_caddy").write_text(web)

    def dotenv(self, key: str) -> str:
        match = re.search(rf"^{key}=(.*)$", (self.app / ".env").read_text(), re.MULTILINE)
        assert match, f"{key} is not in .env"
        return match.group(1)

    # ---- the scripts -----------------------------------------------------
    def env(self, **extra: str) -> dict[str, str]:
        return {
            "FAKE_STATE": str(self.state),
            "APP_DIR": str(self.app),
            "REPO_URL": str(self.origin),
            "BRANCH": "main",
            **GIT_ENV,
            **extra,
        }

    def install(self, **extra: str) -> subprocess.CompletedProcess[str]:
        # every question the script asks is answered (an empty answer is an answer): no tty needed
        answers = {"DOMAIN": "dsos.example.test", "ACME_EMAIL": "", "BACKUP_REPO": "", "GHCR_OWNER": "someone"}
        env = self.env(**{**answers, **extra})
        env.setdefault("FAKE_DPKG_INSTALLED", "docker.io")
        env.setdefault(
            "FAKE_APT_AVAILABLE", "docker-compose-v2 docker-buildx docker-compose-plugin docker-buildx-plugin"
        )
        return run(["bash", REPO / "deploy" / "install.sh"], cwd=self.root, env=self.environ(**env), timeout=180)

    def update(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return run(
            ["bash", self.app / "deploy" / "update.sh", *args],
            cwd=self.app,
            env=self.environ(**self.env(**extra)),
            timeout=180,
        )

    def checkout(self, **env_lines: str) -> None:
        """The clone on the server, with a .env."""
        self.git("clone", "-q", "--branch", "main", str(self.origin), str(self.app), cwd=self.root)
        defaults = {
            "DOMAIN": "dsos.example.test",
            "API_IMAGE": "disciplineos-api:local",
            "WEB_IMAGE": "disciplineos-web:local",
        }
        (self.app / ".env").write_text("".join(f"{k}={v}\n" for k, v in {**defaults, **env_lines}.items()))


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def out(done: subprocess.CompletedProcess[str]) -> str:
    return plain(done.stdout + done.stderr)


def apt_installs(host: Host) -> list[str]:
    return host.calls_like("apt-get install")


# --------------------------------------------------------------------------- #
# 6. install.sh — Docker that is already there stays; only the missing plugins are added
# --------------------------------------------------------------------------- #
def test_docker_io_host_gets_the_ubuntu_compose_and_buildx_packages_and_nothing_else(host: Host) -> None:
    """The first deployment: Ubuntu's docker.io, no compose plugin, no buildx (`docker-compose-plugin` does not exist there)."""
    done = host.install(FAKE_NO_COMPOSE="1", FAKE_NO_BUILDX="1", FAKE_APT_AVAILABLE="docker-compose-v2 docker-buildx")
    assert done.returncode == 0, out(done)
    installs = apt_installs(host)
    assert [c for c in installs if "docker-compose-v2" in c] == [
        "apt-get install -y -qq --no-upgrade --no-install-recommends docker-compose-v2 docker-buildx"
    ]
    apt_calls = [c for c in host.calls if c.startswith("apt-get")]
    for forbidden in (
        "docker-ce",
        "docker.io",
        "containerd",
        "docker-compose-plugin",
        "docker-buildx-plugin",
        " remove",
        " purge",
    ):
        assert not any(forbidden in c for c in apt_calls), forbidden  # the engine on the box is not touched
    assert not any("download.docker.com" in c for c in host.calls)  # no second Docker repository
    assert "keeping it as it is" in out(done)


def test_docker_io_host_that_only_lacks_buildx_gets_just_buildx(host: Host) -> None:
    done = host.install(FAKE_NO_BUILDX="1", FAKE_APT_AVAILABLE="docker-compose-v2 docker-buildx")
    assert done.returncode == 0, out(done)
    assert [c for c in apt_installs(host) if "--no-install-recommends" in c] == [
        "apt-get install -y -qq --no-upgrade --no-install-recommends docker-buildx"
    ]


def test_docker_ce_host_gets_dockers_own_plugin_packages_never_ubuntus(host: Host) -> None:
    """Ubuntu's docker-buildx depends on docker.io, which conflicts with docker-ce: it must not be asked for."""
    done = host.install(
        FAKE_DPKG_INSTALLED="docker-ce docker-ce-cli",
        FAKE_NO_COMPOSE="1",
        FAKE_NO_BUILDX="1",
    )
    assert done.returncode == 0, out(done)
    assert [c for c in apt_installs(host) if "--no-install-recommends" in c] == [
        "apt-get install -y -qq --no-upgrade --no-install-recommends docker-compose-plugin docker-buildx-plugin"
    ]
    assert not any("docker-compose-v2" in c or c.endswith(" docker-buildx") for c in host.calls)


def test_a_host_with_everything_installs_no_plugin_at_all(host: Host) -> None:
    done = host.install()
    assert done.returncode == 0, out(done)
    assert not [c for c in apt_installs(host) if "--no-install-recommends" in c]
    # the tools are installed without ever upgrading what is there (an engine upgrade restarts every container)
    assert all("--no-upgrade" in c for c in apt_installs(host))


def test_an_engine_we_did_not_install_is_left_alone_and_never_gets_apt_plugins(host: Host) -> None:
    """A snap or a static binary: `docker-buildx` from apt would pull in docker.io — better to stop and say so."""
    done = host.install(FAKE_DPKG_INSTALLED="", FAKE_NO_COMPOSE="1")
    assert done.returncode != 0
    assert "never replaces an existing Docker" in out(done)
    assert not [c for c in apt_installs(host) if "--no-install-recommends" in c]


def test_a_docker_io_host_without_any_compose_package_stops_with_an_explanation(host: Host) -> None:
    done = host.install(FAKE_NO_COMPOSE="1", FAKE_APT_AVAILABLE="")
    assert done.returncode != 0
    assert "docker-compose-v2" in out(done) and "by hand" in out(done)
