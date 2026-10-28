"""Helpers shared by the stage-10 ops tests (``test_stage10_ops.py``, ``test_stage10_deploy.py``)."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Script output without its colour codes."""
    return ANSI.sub("", text)


def run(
    argv: Sequence[str | Path],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 120,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a fixed argv (never a shell string) and capture its text output."""
    exe = shutil.which(str(argv[0])) or str(argv[0])
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [exe, *map(str, argv[1:])],
        cwd=cwd,
        env=None if env is None else dict(env),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class Sandbox:
    """``<root>/…`` laid out like a deployment, with fake commands first on ``PATH`` (``<root>/bin``)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        self.env_lines: dict[str, str] = {}

    def copy(self, rel: str) -> Path:
        """A real file of the repository, copied to the same place under the sandbox root."""
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
        """A clean environment (nothing of the developer's DOMAIN, image names, …) with the fakes first."""
        keep = {"LANG", "LC_ALL", "TERM", "TZ", "USER"}
        env = {k: v for k, v in os.environ.items() if k in keep}
        env["PATH"] = f"{self.bin}:{os.environ['PATH']}"
        env["HOME"] = str(self.root)
        env.update(extra)
        return env

    def run(
        self, script: str, *args: str, cwd: Path | None = None, **extra_env: str
    ) -> subprocess.CompletedProcess[str]:
        return run(
            ["bash", self.root / script, *args], cwd=cwd or self.root, env=self.environ(**extra_env), timeout=180
        )
