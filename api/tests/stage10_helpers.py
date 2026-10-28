"""Helpers shared by the stage-10 ops tests (``test_stage10_ops.py``, ``test_stage10_deploy.py``)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
