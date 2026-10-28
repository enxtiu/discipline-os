"""Owner + first-run bootstrap.

``python -m app.cli create-owner`` calls :func:`create_owner`, which creates the
user, their settings row, PARA defaults (areas/tags) and streak counters, and
immediately issues the first pairing code.

Everything here is idempotent: running it twice returns the existing owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import Area, Streak, StreakKind, Tag, User, UserStatus
from app.services.pairing import IssuedPairingCode, create_pairing_code
from app.services.settings_service import get_or_create_settings

log = get_logger(__name__)

DEFAULT_AREAS: tuple[dict[str, Any], ...] = (
    {"name": "Health", "name_ru": "Здоровье", "icon": "heart-pulse", "color": "#22d3ee", "sort_order": 1},
    {"name": "Work", "name_ru": "Работа", "icon": "briefcase", "color": "#a78bfa", "sort_order": 2},
    {"name": "Finance", "name_ru": "Финансы", "icon": "wallet", "color": "#fbbf24", "sort_order": 3},
    {"name": "Learning", "name_ru": "Обучение", "icon": "book-open", "color": "#34d399", "sort_order": 4},
    {"name": "Home", "name_ru": "Дом", "icon": "home", "color": "#f472b6", "sort_order": 5},
    {"name": "Relationships", "name_ru": "Отношения", "icon": "users", "color": "#60a5fa", "sort_order": 6},
    {"name": "Growth", "name_ru": "Развитие", "icon": "sprout", "color": "#fb923c", "sort_order": 7},
)

DEFAULT_TAGS: tuple[dict[str, str], ...] = (
    {"name": "deep-work", "name_ru": "фокус"},
    {"name": "quick", "name_ru": "быстро"},
    {"name": "errand", "name_ru": "поручение"},
    {"name": "call", "name_ru": "звонок"},
    {"name": "health", "name_ru": "здоровье"},
    {"name": "idea", "name_ru": "идея"},
)

DEFAULT_NOTIFICATION_PREFS: dict[str, Any] = {
    "task_reminders": True,
    "meal_logging": True,
    "water": True,
    "workout": True,
    "evening_review": True,
    "streak_at_risk": True,
    "quiet_hours_respected": True,
}

# Weights of the 0..100 daily score — user editable in Settings (stage 8).
DEFAULT_XP_WEIGHTS: dict[str, Any] = {
    "nutrition": 0.30,
    "training": 0.20,
    "steps": 0.15,
    "tasks": 0.20,
    "review": 0.10,
    "consistency": 0.05,
    "xp": {
        "meal_logged": 10,
        "protein_target": 20,
        "fiber_target": 10,
        "water_target": 10,
        "kcal_in_zone": 25,
        "workout_completed": 40,
        "task_p1": 30,
        "task_p2": 20,
        "task_p3": 10,
        "task_p4": 5,
        "timer_hour": 25,
        "daily_review": 25,
    },
}


@dataclass(slots=True)
class OwnerBootstrap:
    user: User
    pairing_code: IssuedPairingCode | None
    created: bool


async def get_owner(db: AsyncSession) -> User | None:
    return (await db.execute(select(User).where(User.is_owner.is_(True)))).scalars().first()


async def create_owner(
    db: AsyncSession,
    *,
    display_name: str = "Owner",
    email: str | None = None,
    max_devices: int | None = None,
    language: str | None = None,
    timezone: str | None = None,
    with_pairing_code: bool = True,
) -> OwnerBootstrap:
    settings = get_settings()
    existing = await get_owner(db)
    if existing is not None:
        code = await create_pairing_code(db, existing, created_by="cli") if with_pairing_code else None
        return OwnerBootstrap(user=existing, pairing_code=code, created=False)

    user = User(
        display_name=display_name,
        email=email,
        status=UserStatus.ACTIVE,
        is_owner=True,
        max_devices=max_devices or settings.max_devices_default,
    )
    db.add(user)
    await db.flush()

    row = await get_or_create_settings(db, user)
    if language:
        row.language = language  # type: ignore[assignment]
    if timezone:
        row.timezone = timezone
        row.timezone_auto = False
    row.notification_prefs = dict(DEFAULT_NOTIFICATION_PREFS)
    row.xp_weights = dict(DEFAULT_XP_WEIGHTS)

    await ensure_defaults(db, user)
    await db.flush()

    code = await create_pairing_code(db, user, created_by="cli") if with_pairing_code else None
    log.info("owner_created", user_id=str(user.id), display_name=user.display_name)
    return OwnerBootstrap(user=user, pairing_code=code, created=True)


async def ensure_defaults(db: AsyncSession, user: User) -> None:
    """Create the rows a brand-new user is expected to have.

    Default areas and tags are named in the user's language (Russian unless the
    settings say English) — they are the user's own rows from then on, so a later
    language switch does not rename them. A default already present under either
    name (any case) is not created again.
    """
    language = (await get_or_create_settings(db, user)).language
    russian = getattr(language, "value", language) != "en"

    def _name(spec: dict[str, Any]) -> str:
        return spec["name_ru"] if russian else spec["name"]

    def _present(spec: dict[str, Any], have: set[str]) -> bool:
        return spec["name"].casefold() in have or spec["name_ru"].casefold() in have

    existing_areas = (await db.execute(select(Area.name).where(Area.user_id == user.id))).scalars().all()
    have = {name.casefold() for name in existing_areas}
    for spec in DEFAULT_AREAS:
        if _present(spec, have):
            continue
        db.add(
            Area(
                user_id=user.id,
                name=_name(spec),
                icon=spec.get("icon"),
                color=spec.get("color"),
                sort_order=spec.get("sort_order", 0),
            )
        )

    existing_tags = (await db.execute(select(Tag.name).where(Tag.user_id == user.id))).scalars().all()
    have_tags = {name.casefold() for name in existing_tags}
    for spec in DEFAULT_TAGS:
        if not _present(spec, have_tags):
            db.add(Tag(user_id=user.id, name=_name(spec)))

    existing_streaks = (await db.execute(select(Streak.kind).where(Streak.user_id == user.id))).scalars().all()
    have_streaks = set(existing_streaks)
    freezes = get_settings().streak_freezes_enabled  # off (the default): nobody starts with a freeze token
    for kind in StreakKind:
        if kind in have_streaks:
            continue
        db.add(Streak(user_id=user.id, kind=kind, freeze_tokens=1 if freezes and kind == StreakKind.OVERALL else 0))

    await db.flush()
