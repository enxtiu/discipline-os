"""Levels: 30 global rows, ``min_total_xp(L) = round(100 · (L−1)^1.6 / 10) · 10`` (L1 = 0).

The curve lives in code (the seed writes the same rows to ``levels``), so a level
is a pure function of the XP total: 0, 100, 300, 580, 920, 1310, … 21 870.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_LEVEL = 30

TITLES: tuple[tuple[str, str], ...] = (
    ("Новичок", "Newcomer"),
    ("Ученик", "Apprentice"),
    ("Практик", "Practitioner"),
    ("Упорный", "Persistent"),
    ("Собранный", "Focused"),
    ("Целеустремлённый", "Driven"),
    ("Дисциплинированный", "Disciplined"),
    ("Стабильный", "Steady"),
    ("Выносливый", "Resilient"),
    ("Мастер привычек", "Habit builder"),
    ("Знаток", "Adept"),
    ("Профи", "Pro"),
    ("Эксперт", "Expert"),
    ("Наставник", "Mentor"),
    ("Стратег", "Strategist"),
    ("Мастер", "Master"),
    ("Ветеран", "Veteran"),
    ("Несгибаемый", "Unbreakable"),
    ("Хозяин режима", "Routine keeper"),
    ("Элита", "Elite"),
    ("Чемпион", "Champion"),
    ("Гроссмейстер", "Grandmaster"),
    ("Виртуоз", "Virtuoso"),
    ("Титан", "Titan"),
    ("Феномен", "Phenomenon"),
    ("Икона", "Icon"),
    ("Мудрец", "Sage"),
    ("Олимпиец", "Olympian"),
    ("Миф", "Myth"),
    ("Легенда", "Legend"),
)
assert len(TITLES) == MAX_LEVEL


def min_total_xp(level: int) -> int:
    """XP needed to reach ``level`` (L1 = 0)."""
    if level <= 1:
        return 0
    return round(100 * (level - 1) ** 1.6 / 10) * 10


@dataclass(slots=True, frozen=True)
class LevelDef:
    level: int
    min_xp: int
    title_ru: str
    title_en: str


LEVELS: tuple[LevelDef, ...] = tuple(
    LevelDef(level=index + 1, min_xp=min_total_xp(index + 1), title_ru=ru, title_en=en)
    for index, (ru, en) in enumerate(TITLES)
)


@dataclass(slots=True, frozen=True)
class LevelProgress:
    current: LevelDef
    next: LevelDef | None
    total_xp: int

    @property
    def progress(self) -> float:
        """0..1 inside the current level; 1.0 at the top level."""
        if self.next is None:
            return 1.0
        span = self.next.min_xp - self.current.min_xp
        return round(min(1.0, max(0.0, (self.total_xp - self.current.min_xp) / span)), 4)


def level_for(total_xp: int) -> LevelProgress:
    total = max(0, int(total_xp))
    current = LEVELS[0]
    for row in LEVELS:
        if row.min_xp <= total:
            current = row
        else:
            break
    following = LEVELS[current.level] if current.level < MAX_LEVEL else None
    return LevelProgress(current=current, next=following, total_xp=total)
