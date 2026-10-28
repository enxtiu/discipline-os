"""Stage 10: the deploy scripts under test (backup, health check, Caddy entrypoint, install, update).

Each script runs for real, in a throw-away copy of the repository layout, with fake ``docker``, ``age``,
``curl``… first on ``PATH`` — no Docker daemon, no network and no root needed. The fakes only stand in
for the outside world; the behaviour that broke on the first real deployment (a SIGPIPE inside a
``set -o pipefail`` pipeline, a glob that did not match the backup layout, …) is the real thing.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from pathlib import Path

import pytest
import yaml

from tests.stage10_helpers import REPO, run

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI.sub("", text)


class Sandbox:
    """``<root>/deploy/…`` (the real scripts), ``<root>/.env`` and ``<root>/bin`` (fake commands)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        for rel in ("deploy/backup/backup.sh", "deploy/healthcheck.sh"):
            self.copy(rel)
        self.env_lines: dict[str, str] = {}

    def copy(self, rel: str) -> Path:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
        return target

    def fake(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def write_env(self, **values: str) -> None:
        self.env_lines.update(values)
        (self.root / ".env").write_text("".join(f"{k}={v}\n" for k, v in self.env_lines.items()))

    def environ(self, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in {"DOMAIN", "BACKUP_LOCAL_DIR", "AGE_IDENTITY_FILE"}}
        env["PATH"] = f"{self.bin}:{os.environ['PATH']}"
        env["HOME"] = str(self.root)
        env.update(extra)
        return env

    def run(self, script: str, *args: str, **extra_env: str):
        return run(["bash", self.root / script, *args], cwd=self.root, env=self.environ(**extra_env), timeout=120)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


# --------------------------------------------------------------------------- #
# 2. backup.sh — the archive check under `set -o pipefail`
# --------------------------------------------------------------------------- #
# A stand-in for age that behaves like the real one where it matters here: "encryption" prefixes the
# recipient, decryption streams the payload and is killed by SIGPIPE when its reader goes away early.
FAKE_AGE = r"""
mode=""; recipient=""; identity=""; output=""; input=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --encrypt) mode=enc ;;
    --decrypt) mode=dec ;;
    --recipient|-r) recipient="$2"; shift ;;
    --identity|-i) identity="$2"; shift ;;
    --output|-o) output="$2"; shift ;;
    *) input="$1" ;;
  esac
  shift
done
if [[ $mode == enc ]]; then
  { echo "FAKE-AGE-FOR:$recipient"; cat; } > "$output"
else
  [[ -r $identity ]] || { echo "age: error: failed to read $identity" >&2; exit 1; }
  [[ "$(head -n 1 "$input")" == "FAKE-AGE-FOR:$(cat "$identity")" ]] \
    || { echo "age: error: no identity matched any of the file's recipients" >&2; exit 1; }
  tail -n +2 "$input"
fi
"""

# `docker compose exec -T postgres pg_dump …` streams the file named by $FAKE_DUMP
FAKE_DOCKER_PG_DUMP = r"""
case "$*" in
  *pg_dump*) cat "$FAKE_DUMP" ;;
  *) echo "fake docker: unexpected call: $*" >&2; exit 1 ;;
esac
"""


def pg_dump_payload(size: int) -> bytes:
    """A custom-format pg_dump starts with the magic «PGDMP», the rest is (incompressible) data."""
    return b"PGDMP" + os.urandom(size)


@pytest.fixture
def backup_box(sandbox: Sandbox) -> Sandbox:
    sandbox.fake("age", FAKE_AGE)
    sandbox.fake("docker", FAKE_DOCKER_PG_DUMP)
    (sandbox.root / "age.key").write_text("recipient-one")
    sandbox.write_env(
        AGE_RECIPIENT="recipient-one",
        AGE_IDENTITY_FILE=str(sandbox.root / "age.key"),
        BACKUP_LOCAL_DIR=str(sandbox.root / "backups"),
        POSTGRES_USER="dsos",
        POSTGRES_DB="dsos",
    )
    return sandbox


def backup(box: Sandbox, payload: bytes):
    dump = box.root / "dump.bin"
    dump.write_bytes(payload)
    return box.run("deploy/backup/backup.sh", "--no-push", FAKE_DUMP=str(dump))


@pytest.mark.parametrize("size", [2_000, 300_000, 3_000_000], ids=["2kB", "300kB", "3MB"])
def test_backup_verifies_a_good_archive_of_any_size(backup_box: Sandbox, size: int) -> None:
    """Bigger than a pipe buffer, `head -c 5` closes the pipe on age (SIGPIPE) — still a good archive."""
    done = backup(backup_box, pg_dump_payload(size))
    out = plain(done.stdout + done.stderr)
    assert done.returncode == 0, out
    assert "WARNING" not in out, out  # the false «could not verify» of the SIGPIPE
    assert "archive verified" in out, out
    written = list((backup_box.root / "backups").glob("*/dsos-*.dump.age"))
    assert len(written) == 1 and oct(written[0].stat().st_mode & 0o777) == "0o600"


def test_backup_warns_when_the_archive_does_not_decrypt_with_the_identity(backup_box: Sandbox) -> None:
    (backup_box.root / "age.key").write_text("someone-elses-key")
    done = backup(backup_box, pg_dump_payload(300_000))
    out = plain(done.stdout + done.stderr)
    assert done.returncode == 0, out  # the file is kept, the operator is told
    assert "WARNING" in out and "does not decrypt" in out and "AGE_RECIPIENT" in out
    assert "archive verified" not in out
    assert len(list((backup_box.root / "backups").glob("*/dsos-*.dump.age"))) == 1


def test_backup_warns_when_the_payload_is_not_a_pg_dump_archive(backup_box: Sandbox) -> None:
    done = backup(backup_box, b"-- plain SQL, not a custom-format archive\n" * 5000)
    out = plain(done.stdout + done.stderr)
    assert done.returncode == 0, out
    assert "WARNING" in out and "pg_dump archive" in out
    assert "archive verified" not in out


def test_backup_says_so_when_it_cannot_verify_without_the_identity(backup_box: Sandbox) -> None:
    (backup_box.root / "age.key").unlink()
    done = backup(backup_box, pg_dump_payload(300_000))
    out = plain(done.stdout + done.stderr)
    assert done.returncode == 0, out
    assert "WARNING" in out and "not readable" in out and "AGE_IDENTITY_FILE" in out


@pytest.mark.skipif(shutil.which("age") is None or shutil.which("age-keygen") is None, reason="age is not installed")
def test_backup_check_works_with_the_real_age(backup_box: Sandbox) -> None:
    key = backup_box.root / "real.key"
    keygen = run(["age-keygen", "-o", key])
    recipient = re.search(r"age1[0-9a-z]+", keygen.stdout + keygen.stderr + key.read_text())
    assert recipient, keygen.stderr
    (backup_box.bin / "age").unlink()  # the real one, found on PATH
    backup_box.write_env(AGE_RECIPIENT=recipient.group(0), AGE_IDENTITY_FILE=str(key))
    done = backup(backup_box, pg_dump_payload(3_000_000))
    out = plain(done.stdout + done.stderr)
    assert done.returncode == 0, out
    assert "archive verified" in out and "WARNING" not in out, out


def verify_step_of_the_workflow(box: Sandbox) -> str:
    """The `run:` text of the nightly-backup workflow's verification step, GitHub expressions filled in."""
    workflow = yaml.safe_load((REPO / ".github/workflows/backup.yml").read_text())
    steps = workflow["jobs"]["backup"]["steps"]
    step = next(s for s in steps if s["name"].startswith("verify freshness"))
    text = step["run"]
    text = text.replace("${{ secrets.DEPLOY_PATH }}", str(box.root))
    return text.replace("${{ vars.BACKUP_WARN_HOURS }}", "")


def test_nightly_workflow_verification_is_not_fooled_by_sigpipe_either(backup_box: Sandbox) -> None:
    """.github/workflows/backup.yml checks the same header over ssh, in a `set -euo pipefail` script."""
    assert backup(backup_box, pg_dump_payload(3_000_000)).returncode == 0
    backup_box.fake("ssh", 'shift\nexec bash -c "$*"\n')  # `ssh dsos "<command>"` → run the command here
    script = backup_box.root / "workflow-step.sh"
    script.write_text(verify_step_of_the_workflow(backup_box))
    # what the GitHub runner does with a `run:` block
    done = run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", script],
        cwd=backup_box.root,
        env=backup_box.environ(),
        timeout=120,
    )
    out = plain(done.stdout + done.stderr)
    assert done.returncode == 0, out
    assert "archive header verified (PGDMP)" in out, out


# --------------------------------------------------------------------------- #
# 3. healthcheck.sh — where the nightly backups are
# --------------------------------------------------------------------------- #
GOOD_HEALTH = (
    '{"status":"ok","database":true,"scheduler":{"running":true,'
    '"jobs":[{"id":"housekeeping","name":"x","next_run_time":"2099-01-01T00:00:00+00:00"}]}}'
)
FAKE_CURL = r"""
url="${*: -1}"
case "$url" in
  *meta/health) [[ -f "$FAKE_HEALTH_JSON" ]] || exit 22; cat "$FAKE_HEALTH_JSON" ;;
  *) echo "$*" >> "${FAKE_CURL_LOG:-/dev/null}" ;;
esac
"""
FAKE_DOCKER_UP = r"""
case "$*" in
  *"compose ps"*) echo '{"State":"running"}' ;;
  *pg_isready*) exit 0 ;;
  *) echo "fake docker: unexpected call: $*" >&2; exit 1 ;;
esac
"""
FAKE_OPENSSL = r"""
for arg in "$@"; do [[ "$arg" == x509 ]] && { echo "notAfter=Jan  1 00:00:00 2099 GMT"; exit 0; }; done
exit 0
"""


@pytest.fixture
def health_box(sandbox: Sandbox) -> Sandbox:
    sandbox.fake("curl", FAKE_CURL)
    sandbox.fake("docker", FAKE_DOCKER_UP)
    sandbox.fake("openssl", FAKE_OPENSSL)
    sandbox.write_env(DOMAIN="dsos.example.test", BACKUP_LOCAL_DIR=str(sandbox.root / "backups"))
    (sandbox.root / "health.json").write_text(GOOD_HEALTH)
    return sandbox


def check_health(box: Sandbox, **extra: str):
    done = box.run("deploy/healthcheck.sh", FAKE_HEALTH_JSON=str(box.root / "health.json"), MIN_FREE_MB="1", **extra)
    return done, plain(done.stdout + done.stderr)


def make_archive(box: Sandbox, rel: str, age_hours: float = 0) -> Path:
    path = box.root / "backups" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def test_healthcheck_finds_the_archive_in_the_stamp_directory_backup_sh_writes(health_box: Sandbox) -> None:
    make_archive(health_box, "20261028T031500Z/dsos-20261028T031500Z.dump.age", age_hours=2)
    make_archive(health_box, "20261027T031500Z/dsos-20261027T031500Z.dump.age", age_hours=26)
    done, out = check_health(health_box)
    assert "✓ last backup 2h old (dsos-20261028T031500Z.dump.age)" in out, out
    assert "no backups found" not in out
    assert done.returncode == 0, out


def test_healthcheck_flags_a_stale_backup(health_box: Sandbox) -> None:
    make_archive(health_box, "20261025T031500Z/dsos-20261025T031500Z.dump.age", age_hours=72)
    done, out = check_health(health_box)
    assert "the newest backup is 72h old" in out, out
    assert done.returncode == 1


def test_healthcheck_reports_an_empty_backup_directory(health_box: Sandbox) -> None:
    (health_box.root / "backups").mkdir()
    done, out = check_health(health_box)
    assert "no backups found" in out and "<stamp>/dsos-<stamp>.dump.age" in out, out
    assert done.returncode == 1


def test_healthcheck_does_not_count_restore_safety_dumps_as_backups(health_box: Sandbox) -> None:
    make_archive(health_box, "pre-restore-20261028T031500Z.dump.age")  # restore.sh drops these next to the stamps
    done, out = check_health(health_box)
    assert "no backups found" in out, out
    assert done.returncode == 1


def test_healthcheck_passes_when_everything_is_fine(health_box: Sandbox) -> None:
    make_archive(health_box, "20261028T031500Z/dsos-20261028T031500Z.dump.age", age_hours=1)
    done, out = check_health(health_box)
    assert done.returncode == 0, out
    assert "All checks passed" in out
