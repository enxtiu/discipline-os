"""Stage 10 ops fixes found by the first real deployment (Ubuntu 24.04, images built on the server).

* ``vapid-keys`` prints a ``.env`` fragment every reader of that file can take: bash (the backup and
  health scripts ``source`` it), python-dotenv (the API) and docker compose (``env_file``);
* ``GET /api/v1/meta/health`` tells the truth about the scheduler in *every* uvicorn worker.

The deploy scripts themselves are covered in ``test_stage10_deploy.py``.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from typer.testing import CliRunner

from app.cli import cli
from tests.stage10_helpers import REPO, run


# --------------------------------------------------------------------------- #
# 1. vapid-keys → a .env fragment that bash, python-dotenv and docker compose all read the same way
# --------------------------------------------------------------------------- #
def _vapid_env_lines() -> list[str]:
    result = CliRunner().invoke(cli, ["vapid-keys"])
    assert result.exit_code == 0, result.output
    return [line for line in result.output.splitlines() if line.startswith("VAPID_")]


def _write_env(tmp_path: Path, lines: list[str]) -> Path:
    """A real .env: the template with its two empty VAPID lines replaced by the CLI's lines."""
    template = (REPO / ".env.example").read_text().splitlines()
    out = [line for line in template if not line.startswith(("VAPID_PUBLIC_KEY=", "VAPID_PRIVATE_KEY="))]
    out.extend(lines)
    path = tmp_path / ".env"
    path.write_text("\n".join(out) + "\n")
    return path


def test_vapid_private_key_is_single_quoted_on_one_line() -> None:
    lines = _vapid_env_lines()
    assert [line.split("=", 1)[0] for line in lines] == ["VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"]
    private = lines[1].split("=", 1)[1]
    # the PEM has spaces: only the quotes keep «PRIVATE» from being run as a command by `. ./.env`
    assert private.startswith("'-----BEGIN PRIVATE KEY-----\\n") and private.endswith("-----END PRIVATE KEY-----\\n'")
    assert "\n" not in private and "'" not in private[1:-1]


def test_vapid_env_lines_are_read_the_same_by_bash_and_python_dotenv(tmp_path: Path) -> None:
    env = _write_env(tmp_path, _vapid_env_lines())
    expected = dotenv_values(env)["VAPID_PRIVATE_KEY"]
    assert expected and expected.startswith("-----BEGIN PRIVATE KEY-----\\n") and expected.endswith("\\n")
    assert "'" not in expected

    # deploy/backup/backup.sh, restore.sh and deploy/healthcheck.sh all start with `set -a; . ./.env`
    script = tmp_path / "source_env.sh"
    script.write_text(
        'set -euo pipefail\nset -a\n. "$1"\nset +a\nprintf %s "$VAPID_PRIVATE_KEY"\n'
        'printf "\\0%s" "$VAPID_PUBLIC_KEY"\n'
    )
    proc = run(["bash", script, env], timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", proc.stderr  # no «PRIVATE: command not found»
    private, public = proc.stdout.split("\0")
    assert private == expected
    assert public == dotenv_values(env)["VAPID_PUBLIC_KEY"]


def _compose_available() -> bool:
    return run(["docker", "compose", "version"], timeout=60).returncode == 0


@pytest.mark.skipif(not _compose_available(), reason="the docker compose plugin is not installed")
def test_vapid_env_lines_are_literal_for_docker_compose(tmp_path: Path) -> None:
    """compose keeps a single-quoted value as it is (a double-quoted one would get real line breaks)."""
    env = _write_env(tmp_path, _vapid_env_lines())
    (tmp_path / "compose.yml").write_text("services:\n  api:\n    image: busybox\n    env_file: [.env]\n")
    proc = run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "COMPOSE_PROJECT_NAME": "dsos-test"},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    environment = json.loads(proc.stdout)["services"]["api"]["environment"]
    values = dotenv_values(stream=io.StringIO(env.read_text()))
    assert environment["VAPID_PRIVATE_KEY"] == values["VAPID_PRIVATE_KEY"]
    assert environment["VAPID_PUBLIC_KEY"] == values["VAPID_PUBLIC_KEY"]
