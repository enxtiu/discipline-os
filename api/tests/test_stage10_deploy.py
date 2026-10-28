"""Stage 10: the deploy scripts under test (backup, health check, Caddy entrypoint, install, update).

Each script runs for real, in a throw-away copy of the repository layout, with fake ``docker``, ``age``,
``curl``… first on ``PATH`` — no Docker daemon, no network and no root needed. The fakes only stand in
for the outside world; the behaviour that broke on the first real deployment (a SIGPIPE inside a
``set -o pipefail`` pipeline, a glob that did not match the backup layout, …) is the real thing.
"""

from __future__ import annotations

import json
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


# --------------------------------------------------------------------------- #
# 4. healthcheck.sh — the scheduler as /meta/health reports it (the same from every api worker)
# --------------------------------------------------------------------------- #
JOBS = [
    {"id": "scheduler_heartbeat", "name": "heartbeat", "next_run_time": "2026-10-28T22:08:28+07:00"},
    {"id": "dispatch_notifications", "name": "dispatch", "next_run_time": "2026-10-28T22:08:28+07:00"},
    {"id": "housekeeping", "name": "housekeeping", "next_run_time": "2026-10-28T22:22:28+07:00"},
]


def scheduler(**overrides) -> dict:
    """The `scheduler` object of a healthy deployment, as seen by a worker that does NOT own it."""
    base = {
        "enabled": True,
        "running": True,
        "owner": False,
        "lock_held": True,
        "started_at": "2026-10-28T15:07:28+00:00",
        "heartbeat_age_seconds": 12,
        "stale": False,
        "jobs": JOBS,
    }
    return base | overrides


def health_body(sched: dict | None, **top) -> str:
    return json.dumps({"status": "ok", "database": True, "scheduler": sched} | top)


def run_check(box: Sandbox, body: str):
    (box.root / "health.json").write_text(body)
    make_archive(box, "20261028T031500Z/dsos-20261028T031500Z.dump.age", age_hours=1)
    return check_health(box)


def test_healthcheck_accepts_the_scheduler_whichever_worker_answered(health_box: Sandbox) -> None:
    done, out = run_check(health_box, health_body(scheduler()))  # a worker that does not own it
    assert done.returncode == 0, out
    assert "scheduler: running=True jobs=3 owner=False heartbeat=12s" in out
    done, out = run_check(health_box, health_body(scheduler(owner=True, heartbeat_age_seconds=4)))  # the owner itself
    assert done.returncode == 0, out
    assert "owner=True heartbeat=4s" in out


def test_healthcheck_still_reads_the_older_api_answer(health_box: Sandbox) -> None:
    """Right after the script is updated and before the api is: running/jobs only, no new keys."""
    old = {"running": True, "jobs": JOBS}
    done, out = run_check(health_box, health_body(old))
    assert done.returncode == 0, out
    assert "scheduler: running=True jobs=3" in out and "owner=" not in out


@pytest.mark.parametrize(
    ("sched", "problem"),
    [
        pytest.param(
            {"enabled": True, "running": False, "owner": False, "lock_held": False, "stale": False, "jobs": []},
            "no scheduler is running: nothing holds the scheduler lock",
            id="no-one-holds-the-lock",
        ),
        pytest.param(
            {"enabled": False, "running": False, "owner": False, "lock_held": False, "stale": False, "jobs": []},
            "the scheduler is disabled (SCHEDULER_ENABLED=false)",
            id="disabled",
        ),
        pytest.param({"running": False, "jobs": []}, "the scheduler is not running", id="older-api-no-scheduler"),
        pytest.param(
            scheduler(stale=True, heartbeat_age_seconds=640),
            "has not published its state for 640 s",
            id="stale-heartbeat",
        ),
        pytest.param(
            scheduler(stale=True, heartbeat_age_seconds=None, jobs=[]),
            "has not published its state at all",
            id="nothing-published",
        ),
        pytest.param(scheduler(jobs=[]), "the scheduler has no jobs registered", id="no-jobs"),
        pytest.param(
            scheduler(jobs=[*JOBS[:2], {"id": "notion_sync", "name": "n", "next_run_time": None}]),
            "a scheduler job is paused (no next run): notion_sync",
            id="paused-job",
        ),
        pytest.param(
            scheduler(owner=True, lock_held=False),
            "the lock that keeps a second one away is gone",
            id="lock-lost",
        ),
    ],
)
def test_healthcheck_names_what_is_wrong_with_the_scheduler(health_box: Sandbox, sched: dict, problem: str) -> None:
    done, out = run_check(health_box, health_body(sched))
    assert problem in out, out
    assert done.returncode == 1, out
    assert "payload ok" not in out


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (health_body(scheduler(), status="degraded"), "health status is 'degraded', not 'ok'"),
        (health_body(scheduler(), database=False), "the database flag is false"),
        (health_body(None), "the scheduler is not running"),
        ("<html>bad gateway</html>", "did not answer a JSON object"),
        ("[1, 2]", "did not answer a JSON object"),
    ],
    ids=["degraded", "no-database", "no-scheduler-object", "html", "json-array"],
)
def test_healthcheck_names_what_is_wrong_with_the_payload(health_box: Sandbox, body: str, problem: str) -> None:
    done, out = run_check(health_box, body)
    assert problem in out, out
    assert done.returncode == 1, out


# --------------------------------------------------------------------------- #
# 5. Caddy — the ACME account e-mail is optional (docker/caddy-entrypoint.sh + the Caddyfile import)
# --------------------------------------------------------------------------- #
CADDY = os.environ.get("CADDY_BIN") or shutil.which("caddy")
needs_caddy = pytest.mark.skipif(CADDY is None, reason="no caddy binary (set CADDY_BIN or put caddy on PATH)")
ENTRYPOINT = REPO / "docker" / "caddy-entrypoint.sh"


@pytest.fixture
def caddy_dir(tmp_path: Path) -> Path:
    """What the image has in /etc/caddy: the Caddyfile and the comment-only default snippet."""
    target = tmp_path / "etc-caddy"
    target.mkdir()
    shutil.copy2(REPO / "docker" / "Caddyfile", target)
    shutil.copy2(REPO / "docker" / "acme-email.caddy", target)
    return target


def start_container(caddy_dir: Path, email: str | None, *command: str):
    """What the image's ENTRYPOINT does before it hands over to `caddy run`."""
    env = {"PATH": os.environ["PATH"], "CADDY_ACME_EMAIL_FILE": str(caddy_dir / "acme-email.caddy")}
    if email is not None:
        env["ACME_EMAIL"] = email
    return run(["sh", ENTRYPOINT, *(command or ("true",))], env=env, timeout=30)


def snippet_directives(caddy_dir: Path) -> list[str]:
    lines = (caddy_dir / "acme-email.caddy").read_text().splitlines()
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def issuer_emails(caddy_dir: Path) -> list[str | None] | None:
    """The e-mail the real caddy puts into the ACME issuers of the adapted Caddyfile (None = no TLS app)."""
    adapted = run(
        [CADDY or "caddy", "adapt", "--config", caddy_dir / "Caddyfile", "--adapter", "caddyfile"],
        env={"PATH": os.environ["PATH"], "DOMAIN": "dsos.example.com", "HOME": str(caddy_dir)},
        timeout=60,
    )
    assert adapted.returncode == 0, adapted.stderr
    tls = json.loads(adapted.stdout)["apps"].get("tls")
    if not tls:
        return None
    return [issuer.get("email") for issuer in tls["automation"]["policies"][0]["issuers"]]


def caddy_validate(caddy_dir: Path):
    return run(
        [CADDY or "caddy", "validate", "--config", caddy_dir / "Caddyfile", "--adapter", "caddyfile"],
        env={"PATH": os.environ["PATH"], "DOMAIN": "dsos.example.com", "HOME": str(caddy_dir)},
        timeout=60,
    )


@pytest.mark.parametrize(
    ("email", "directive"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        ("me@example.com", ['email "me@example.com"']),
        ("  me@example.com  ", ['email "me@example.com"']),
        ("me@example.com\r", ['email "me@example.com"']),  # a .env with CRLF line endings
        ("first.last+dsos@mail.example.co.uk", ['email "first.last+dsos@mail.example.co.uk"']),
    ],
    ids=["unset", "empty", "blanks", "address", "padded", "crlf", "plus-and-dots"],
)
def test_entrypoint_writes_the_email_directive_only_when_there_is_an_address(
    caddy_dir: Path, email: str | None, directive: list[str]
) -> None:
    done = start_container(caddy_dir, email)
    assert done.returncode == 0, done.stderr
    assert snippet_directives(caddy_dir) == directive
    assert done.stderr == ""


@pytest.mark.parametrize(
    "email",
    [
        "me @example.com",
        'me"@example.com',
        "me@{env.ACME}",
        "me@example.com\nbind 1.2.3.4",
        "no-at-sign",
        "a@b@c.d",
        "@x.y",
        "me@",
    ],
    ids=["space", "quote", "placeholder", "newline-injection", "no-at", "two-ats", "no-local-part", "no-domain"],
)
def test_entrypoint_ignores_a_value_that_is_not_an_email_address(caddy_dir: Path, email: str) -> None:
    done = start_container(caddy_dir, email)
    assert done.returncode == 0, done.stderr  # the site still comes up
    assert snippet_directives(caddy_dir) == []
    assert "does not look like an e-mail address" in done.stderr


def test_entrypoint_hands_over_to_its_command_and_keeps_its_exit_code(caddy_dir: Path) -> None:
    done = start_container(caddy_dir, "me@example.com", "sh", "-c", 'echo "started with $0 $1"; exit 3', "a", "b")
    assert done.returncode == 3
    assert done.stdout.strip() == "started with a b"
    assert snippet_directives(caddy_dir) == ['email "me@example.com"']  # written before the command ran


def test_entrypoint_rewrites_the_snippet_on_every_start(caddy_dir: Path) -> None:
    start_container(caddy_dir, "me@example.com")
    assert snippet_directives(caddy_dir) == ['email "me@example.com"']
    start_container(caddy_dir, "")  # the address was taken out of .env and the container restarted
    assert snippet_directives(caddy_dir) == []


def test_entrypoint_still_starts_when_it_cannot_write_the_snippet(tmp_path: Path) -> None:
    env = {"PATH": os.environ["PATH"], "CADDY_ACME_EMAIL_FILE": str(tmp_path / "missing-dir" / "acme-email.caddy")}
    env["ACME_EMAIL"] = "me@example.com"
    done = run(["sh", ENTRYPOINT, "echo", "up"], env=env, timeout=30)
    assert done.returncode == 0 and done.stdout.strip() == "up"
    assert "cannot write" in done.stderr


@needs_caddy
def test_the_caddyfile_as_committed_is_valid_without_any_acme_email() -> None:
    """The repo's own docker/ directory is a working /etc/caddy: CI (or you) can `caddy validate` it as it is."""
    done = run(
        [CADDY or "caddy", "validate", "--config", REPO / "docker" / "Caddyfile", "--adapter", "caddyfile"],
        env={"PATH": os.environ["PATH"], "DOMAIN": "dsos.example.com", "HOME": str(REPO)},
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "Valid configuration" in done.stdout + done.stderr


@needs_caddy
@pytest.mark.parametrize(
    "email", [None, "", "   ", "not an address", "me@example.com"], ids=["unset", "empty", "blanks", "bad", "good"]
)
def test_caddy_starts_with_and_without_the_acme_email(caddy_dir: Path, email: str | None) -> None:
    """The bug: an empty ACME_EMAIL made `email {$ACME_EMAIL}` a Caddyfile parse error."""
    assert start_container(caddy_dir, email).returncode == 0
    done = caddy_validate(caddy_dir)
    assert done.returncode == 0, done.stderr
    assert "Valid configuration" in done.stdout + done.stderr
    expected = ["me@example.com", "me@example.com"] if email == "me@example.com" else None
    assert issuer_emails(caddy_dir) == expected  # the address reaches both ACME issuers; otherwise none is set


@needs_caddy
def test_the_old_caddyfile_really_broke_on_an_empty_email(caddy_dir: Path) -> None:
    """The regression guard for the guard: with the previous directive the same input does not parse."""
    old = (caddy_dir / "Caddyfile").read_text().replace("import acme-email.caddy", "email {$ACME_EMAIL}")
    (caddy_dir / "Caddyfile").write_text(old)
    broken = run(
        [CADDY or "caddy", "validate", "--config", caddy_dir / "Caddyfile", "--adapter", "caddyfile"],
        env={"PATH": os.environ["PATH"], "DOMAIN": "dsos.example.com", "ACME_EMAIL": "", "HOME": str(caddy_dir)},
        timeout=60,
    )
    assert broken.returncode != 0 and "wrong argument count" in broken.stderr


def test_the_image_and_compose_wire_the_entrypoint_up() -> None:
    dockerfile = (REPO / "docker" / "caddy.Dockerfile").read_text()
    assert 'ENTRYPOINT ["/usr/local/bin/caddy-entrypoint.sh"]' in dockerfile
    assert 'CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]' in dockerfile
    assert "docker/acme-email.caddy" in dockerfile and "docker/caddy-entrypoint.sh" in dockerfile
    assert "email {$ACME_EMAIL}" not in (REPO / "docker" / "Caddyfile").read_text()
    assert (
        "ACME_EMAIL: ${ACME_EMAIL:-}" in (REPO / "docker-compose.yml").read_text()
    )  # no «variable is not set» warning
