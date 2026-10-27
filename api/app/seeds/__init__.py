"""
Seed framework.

Every seed is an idempotent coroutine registered in :data:`SEEDS`. Running the
same seed twice must not create duplicates — that is what makes it safe to call
from an entrypoint, from CI, or after a restore:

    python -m app.cli seed --list
    python -m app.cli seed exercises

Stage 1 ships the **exercise library** (the training screens of stage 4 read it)
plus the framework itself. Later stages register their data here:

  * stage 2 — `foods.py`   : the base food table (Russian + English names)
  * stage 8 — `gamification.py` : levels, achievements, the quest pool

Seeds never touch user data: global rows only (`user_id IS NULL` / `is_global`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

log = get_logger(__name__)

Seeder = Callable[[AsyncSession], Awaitable[dict[str, int]]]


@dataclass(frozen=True)
class Seed:
    name: str
    description: str
    run: Seeder
    tags: tuple[str, ...] = field(default_factory=tuple)


def _registry() -> dict[str, Seed]:
    # Imported lazily so a missing stage-2 module cannot break `seed --list`.
    from app.seeds.exercises import SEED as exercises_seed
    from app.seeds.gamification import SEED as gamification_seed

    seeds = [exercises_seed, gamification_seed]
    return {seed.name: seed for seed in seeds}


def list_seeds() -> list[Seed]:
    return list(_registry().values())


def get_seed(name: str) -> Seed:
    registry = _registry()
    if name not in registry:
        known = ", ".join(sorted(registry)) or "<none>"
        raise KeyError(f"unknown seed {name!r} (known: {known})")
    return registry[name]


async def run_seeds(session: AsyncSession, names: list[str] | None = None) -> dict[str, Any]:
    """Run the requested seeds (all of them when *names* is empty)."""
    selected = [get_seed(name) for name in names] if names else list_seeds()
    report: dict[str, Any] = {}
    for seed in selected:
        log.info("seed_started", seed=seed.name)
        counts = await seed.run(session)
        await session.commit()
        log.info("seed_finished", seed=seed.name, **counts)
        report[seed.name] = counts
    return report
