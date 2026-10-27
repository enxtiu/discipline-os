"""Gamification reference data (stage 8): 30 levels, 46 achievements, the quest pool.

Global rows only (``user_id IS NULL``), keyed by ``level`` / ``code``: re-running the
seed updates texts, rewards and criteria in place and never duplicates.

    python -m app.cli seed gamification

Criteria are machine-readable: ``{"metric": "<name>", "gte": N}`` over the metrics
computed in ``app/services/gamification/achievements.py`` (achievements) and
``quests.py`` (quests; ``"target": "<name>"`` = a per-user target such as your
water goal, ``"requires": [...]`` = when the quest makes sense for you). No
achievement or quest rewards low intake, fasting, weight-loss speed or burned
calories; intake-tied ones count only days at or above the safe floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Achievement, AchievementRarity, Level, Quest, QuestKind
from app.seeds import Seed
from app.services.gamification.levels import LEVELS

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AchievementDef:
    code: str
    category: str
    rarity: str
    metric: str
    gte: int
    xp: int
    name_ru: str
    name_en: str
    description_ru: str
    description_en: str
    icon: str
    secret: bool = False


@dataclass(frozen=True, slots=True)
class QuestDef:
    code: str
    kind: str
    metric: str
    xp: int
    title_ru: str
    title_en: str
    description_ru: str
    description_en: str
    icon: str
    gte: int | None = None
    target: str | None = None  # a per-user target instead of a fixed number
    unit: str = "count"
    requires: tuple[str, ...] = field(default_factory=tuple)

    def criteria(self) -> dict[str, Any]:
        out: dict[str, Any] = {"metric": self.metric, "unit": self.unit}
        if self.gte is not None:
            out["gte"] = self.gte
        if self.target is not None:
            out["target"] = self.target
        if self.requires:
            out["requires"] = list(self.requires)
        return out


A = AchievementDef
ACHIEVEMENTS: tuple[AchievementDef, ...] = (
    # ---- nutrition (logging and targets; intake days count only at or above the floor)
    A("meals_1", "nutrition", "common", "meals_logged_total", 1, 10, "Первая запись", "First entry",
      "Запишите первый приём пищи", "Log your first meal", "utensils"),
    A("meals_50", "nutrition", "common", "meals_logged_total", 50, 30, "Полсотни записей", "Fifty entries",
      "Запишите 50 приёмов пищи", "Log 50 meals", "notebook"),
    A("meals_250", "nutrition", "rare", "meals_logged_total", 250, 80, "Летописец питания", "Food chronicler",
      "Запишите 250 приёмов пищи", "Log 250 meals", "book"),
    A("meals_1000", "nutrition", "epic", "meals_logged_total", 1000, 200, "Архивариус", "Archivist",
      "Запишите 1000 приёмов пищи", "Log 1,000 meals", "library"),
    A("protein_7", "nutrition", "common", "days_protein_target", 7, 30, "Белковый ритм", "Protein rhythm",
      "7 дней с белком по цели", "Hit your protein target on 7 days", "egg"),
    A("protein_30", "nutrition", "rare", "days_protein_target", 30, 100, "Строитель", "Builder",
      "30 дней с белком по цели", "Hit your protein target on 30 days", "dumbbell"),
    A("water_7", "nutrition", "common", "days_water_target", 7, 25, "Водный баланс", "Hydrated",
      "7 дней с нормой воды", "Reach your water target on 7 days", "droplet"),
    A("water_30", "nutrition", "rare", "days_water_target", 30, 80, "Как рыба в воде", "Like a fish in water",
      "30 дней с нормой воды", "Reach your water target on 30 days", "waves"),
    # ---- training
    A("workout_1", "training", "common", "workouts_total", 1, 15, "Первая тренировка", "First workout",
      "Завершите первую тренировку", "Finish your first workout", "dumbbell"),
    A("workouts_25", "training", "rare", "workouts_total", 25, 80, "В ритме", "In the groove",
      "Завершите 25 тренировок", "Finish 25 workouts", "activity"),
    A("workouts_100", "training", "epic", "workouts_total", 100, 200, "Сотня тренировок", "Century",
      "Завершите 100 тренировок", "Finish 100 workouts", "medal"),
    A("pr_1", "training", "common", "prs_total", 1, 20, "Личный рекорд", "Personal best",
      "Поставьте первый личный рекорд", "Set your first personal record", "trophy"),
    A("prs_25", "training", "rare", "prs_total", 25, 100, "Рекордсмен", "Record breaker",
      "Поставьте 25 личных рекордов", "Set 25 personal records", "trophy"),
    # ---- activity, sleep, weigh-ins (counts of logging — never weight change)
    A("steps_10k", "activity", "common", "steps_best_day", 10000, 20, "10 000 шагов", "10k day",
      "Пройдите 10 000 шагов за день", "Walk 10,000 steps in a day", "footprints"),
    A("steps_20k", "activity", "rare", "steps_best_day", 20000, 50, "Большая прогулка", "Long walk",
      "Пройдите 20 000 шагов за день", "Walk 20,000 steps in a day", "map"),
    A("steps_target_7", "activity", "common", "days_steps_target", 7, 30, "Неделя в движении", "Week on the move",
      "7 дней с целью по шагам", "Reach your step target on 7 days", "footprints"),
    A("steps_target_50", "activity", "rare", "days_steps_target", 50, 100, "Ходок", "Walker",
      "50 дней с целью по шагам", "Reach your step target on 50 days", "mountain"),
    A("sleep_7", "activity", "common", "nights_7h_total", 7, 25, "Выспался", "Well rested",
      "7 ночей по 7 часов и больше", "Sleep 7 hours or more on 7 nights", "moon"),
    A("sleep_30", "activity", "rare", "nights_7h_total", 30, 80, "Режим сна", "Sleep routine",
      "30 ночей по 7 часов и больше", "Sleep 7 hours or more on 30 nights", "moon"),
    A("weigh_ins_10", "activity", "common", "weigh_ins_total", 10, 25, "Регулярные замеры", "Regular check-ins",
      "Запишите вес 10 раз", "Log 10 weigh-ins", "scale"),
    # ---- planner
    A("task_1", "planner", "common", "tasks_completed_total", 1, 10, "Первая галочка", "First tick",
      "Выполните первую задачу", "Complete your first task", "check"),
    A("tasks_100", "planner", "rare", "tasks_completed_total", 100, 80, "Сотня дел", "Hundred done",
      "Выполните 100 задач", "Complete 100 tasks", "check-check"),
    A("tasks_500", "planner", "epic", "tasks_completed_total", 500, 200, "Мастер дел", "Getting things done",
      "Выполните 500 задач", "Complete 500 tasks", "list-checks"),
    A("p1_25", "planner", "rare", "p1_tasks_completed_total", 25, 80, "Главное — первым", "First things first",
      "Выполните 25 задач P1", "Complete 25 P1 tasks", "flag"),
    A("project_1", "planner", "common", "projects_completed_total", 1, 30, "Проект завершён", "Project shipped",
      "Завершите проект", "Complete a project", "folder-check"),
    A("projects_10", "planner", "epic", "projects_completed_total", 10, 150, "Финишёр", "Finisher",
      "Завершите 10 проектов", "Complete 10 projects", "rocket"),
    A("focus_10h", "planner", "common", "tracked_minutes_total", 600, 30, "10 часов фокуса", "10 hours of focus",
      "Отследите 10 часов работы", "Track 10 hours", "timer"),
    A("focus_100h", "planner", "epic", "tracked_minutes_total", 6000, 200, "Глубокая работа", "Deep work",
      "Отследите 100 часов работы", "Track 100 hours", "hourglass"),
    # ---- review
    A("review_1", "review", "common", "journal_entries_total", 1, 10, "Первые итоги", "First review",
      "Подведите итоги дня", "Write your first daily review", "pen"),
    A("reviews_30", "review", "rare", "journal_entries_total", 30, 80, "Рефлексия", "Reflective",
      "Подведите итоги 30 дней", "Write 30 daily reviews", "book-open"),
    A("reviews_100", "review", "epic", "journal_entries_total", 100, 150, "Летопись дней", "Chronicle",
      "Подведите итоги 100 дней", "Write 100 daily reviews", "scroll"),
    # ---- streaks
    A("streak_overall_7", "streaks", "rare", "streak_longest_overall", 7, 50, "Неделя в ритме", "Week in rhythm",
      "Счёт дня 60+ семь дней подряд", "Daily score 60+ seven days in a row", "flame"),
    A("streak_overall_30", "streaks", "epic", "streak_longest_overall", 30, 150, "Месяц дисциплины",
      "Month of discipline", "Счёт дня 60+ тридцать дней подряд", "Daily score 60+ thirty days in a row", "flame"),
    A("streak_overall_100", "streaks", "legendary", "streak_longest_overall", 100, 400, "Сто дней", "Hundred days",
      "Счёт дня 60+ сто дней подряд", "Daily score 60+ a hundred days in a row", "crown", True),
    A("streak_meals_30", "streaks", "rare", "streak_longest_meal_logging", 30, 80, "Месяц записей", "Logging month",
      "Записывайте еду 30 дней подряд", "Log food 30 days in a row", "calendar-check"),
    A("streak_workout_8", "streaks", "epic", "streak_longest_workout", 8, 150, "Восемь недель по плану",
      "Eight weeks on plan", "Тренировки по плану 8 недель подряд", "Planned workouts 8 weeks in a row",
      "calendar-range"),
    A("streak_review_14", "streaks", "rare", "streak_longest_review", 14, 50, "Две недели итогов",
      "Two weeks of reviews", "Итоги дня 14 дней подряд", "Daily review 14 days in a row", "pen-line"),
    # ---- levels
    A("level_5", "levels", "common", "level", 5, 25, "Уровень 5", "Level 5",
      "Достигните 5-го уровня", "Reach level 5", "star"),
    A("level_10", "levels", "rare", "level", 10, 50, "Уровень 10", "Level 10",
      "Достигните 10-го уровня", "Reach level 10", "star"),
    A("level_20", "levels", "epic", "level", 20, 100, "Уровень 20", "Level 20",
      "Достигните 20-го уровня", "Reach level 20", "stars"),
    A("level_30", "levels", "legendary", "level", 30, 250, "Вершина", "Summit",
      "Достигните 30-го уровня", "Reach level 30", "gem"),
    # ---- special (score-based days count only with credit: a meal logged, intake ≥ the floor)
    A("perfect_day", "special", "rare", "perfect_days", 1, 50, "Идеальный день", "Perfect day",
      "Счёт дня 95 и выше", "Score 95 or more in a day", "sparkles", True),
    A("perfect_10", "special", "legendary", "perfect_days", 10, 300, "Десять идеальных", "Ten perfect days",
      "10 дней со счётом 95+", "Score 95+ on 10 days", "sparkles", True),
    A("strong_week", "special", "rare", "days_score_70_in_week", 5, 75, "Сильная неделя", "Strong week",
      "5 дней одной недели со счётом 70+", "Score 70+ on 5 days of one week", "zap"),
    A("flawless_week", "special", "epic", "days_score_70_in_week", 7, 150, "Безупречная неделя", "Flawless week",
      "Все 7 дней недели со счётом 70+", "Score 70+ on all 7 days of a week", "shield-check", True),
    A("quests_10", "special", "rare", "quests_claimed_total", 10, 50, "Искатель", "Quest seeker",
      "Получите награду за 10 заданий", "Claim 10 quests", "compass"),
)  # fmt: skip

Q = QuestDef
QUESTS: tuple[QuestDef, ...] = (
    # ---- daily (3 per day, chosen per user and date from the ones that make sense)
    Q("daily_meals_3", "daily", "meals_logged", 20, "Запишите 3 приёма пищи", "Log 3 meals",
      "Всё, что съели, — в дневник", "Everything you eat, in the log", "utensils", gte=3),
    Q("daily_water", "daily", "water_ml", 20, "Выпейте норму воды", "Reach your water target",
      "Отмечайте воду в течение дня", "Log your water during the day", "droplet", target="water_ml", unit="ml",
      requires=("water_target",)),
    Q("daily_protein", "daily", "protein_target", 25, "Белок по цели", "Hit your protein target",
      "Белок за день — по вашей цели", "Today's protein reaches your target", "egg", gte=1,
      requires=("protein_target",)),
    Q("daily_steps", "daily", "steps", 20, "Шаги по цели", "Reach your step target",
      "Пройдите свою дневную цель", "Walk your daily goal", "footprints", target="steps", unit="steps",
      requires=("steps_target",)),
    Q("daily_tasks_3", "daily", "tasks_completed", 20, "Выполните 3 задачи", "Complete 3 tasks",
      "Три галочки за день", "Three ticks today", "check", gte=3, requires=("open_tasks_3",)),
    Q("daily_focus_60", "daily", "tracked_minutes", 20, "60 минут фокуса", "Track 60 minutes",
      "Запустите таймер на задачу", "Run the timer on a task", "timer", gte=60, unit="min",
      requires=("uses_timer",)),
    Q("daily_review", "daily", "journal", 15, "Подведите итоги дня", "Write today's review",
      "Пара строк или оценка дня", "A few lines or a rating", "pen", gte=1),
    Q("daily_sleep", "daily", "sleep_logged", 10, "Отметьте сон", "Log last night's sleep",
      "Время отхода ко сну и подъёма", "Bed and wake-up time", "moon", gte=1),
    Q("daily_workout", "daily", "workouts", 30, "Тренировка по плану", "Do today's planned workout",
      "Сегодня в плане тренировка", "Your plan has a workout today", "dumbbell", gte=1,
      requires=("planned_workout_day",)),
    Q("daily_p1", "daily", "p1_tasks_completed", 20, "Задача P1", "Complete a P1 task",
      "Самое важное — первым", "The most important first", "flag", gte=1, requires=("open_p1",)),
    # ---- weekly (2 per week)
    Q("weekly_workouts", "weekly", "workouts", 80, "Тренировки недели", "This week's workouts",
      "Столько, сколько в плане (без плана — 3)", "As many as planned (3 without a plan)", "calendar-range",
      target="planned_workouts", requires=("planned_workouts_week",)),
    Q("weekly_protein_5", "weekly", "days_protein_target", 75, "Белок по цели: 5 дней", "Protein target on 5 days",
      "Пять дней недели с белком по цели", "Five days this week on your protein target", "egg", gte=5,
      unit="days", requires=("protein_target",)),
    Q("weekly_review_5", "weekly", "days_journal", 60, "Итоги дня: 5 раз", "Daily review on 5 days",
      "Пять вечеров с итогами", "Five evenings with a review", "pen-line", gte=5, unit="days"),
    Q("weekly_steps_5", "weekly", "days_steps_target", 60, "Шаги по цели: 5 дней", "Step target on 5 days",
      "Пять дней недели с целью по шагам", "Five days this week on your step goal", "footprints", gte=5,
      unit="days", requires=("steps_target",)),
    Q("weekly_tasks_15", "weekly", "tasks_completed", 75, "15 задач за неделю", "Complete 15 tasks",
      "Пятнадцать галочек за неделю", "Fifteen ticks this week", "list-checks", gte=15,
      requires=("uses_planner",)),
    Q("weekly_focus_10h", "weekly", "tracked_minutes", 75, "10 часов фокуса", "Track 10 hours",
      "Десять часов с таймером за неделю", "Ten hours on the timer this week", "hourglass", gte=600, unit="min",
      requires=("uses_timer",)),
    Q("weekly_water_5", "weekly", "days_water_target", 60, "Норма воды: 5 дней", "Water target on 5 days",
      "Пять дней недели с нормой воды", "Five days this week on your water goal", "droplet", gte=5,
      unit="days", requires=("water_target",)),
)  # fmt: skip


SEED_LOCK_KEY = 918_273_646  # transaction advisory lock: concurrent seeders take turns


async def seed_gamification(session: AsyncSession) -> dict[str, int]:
    """Upsert levels (by level), achievements and quests (by code)."""
    await session.execute(select(func.pg_advisory_xact_lock(SEED_LOCK_KEY)))
    counts = {"levels": 0, "achievements": 0, "quests": 0, "created": 0}

    levels = {row.level: row for row in (await session.scalars(select(Level))).all()}
    for spec in LEVELS:
        row = levels.get(spec.level)
        if row is None:
            row = Level(level=spec.level)
            session.add(row)
            counts["created"] += 1
        row.user_id = None
        row.min_total_xp = spec.min_xp
        row.title_ru = spec.title_ru
        row.title_en = spec.title_en
        counts["levels"] += 1

    achievements = {row.code: row for row in (await session.scalars(select(Achievement))).all()}
    for order, spec in enumerate(ACHIEVEMENTS):
        row = achievements.get(spec.code)
        if row is None:
            row = Achievement(code=spec.code)
            session.add(row)
            counts["created"] += 1
        row.name_ru, row.name_en = spec.name_ru, spec.name_en
        row.description_ru, row.description_en = spec.description_ru, spec.description_en
        row.icon = spec.icon
        row.category = spec.category
        row.rarity = AchievementRarity(spec.rarity)
        row.criteria = {"metric": spec.metric, "gte": spec.gte}
        row.target_value = Decimal(spec.gte)
        row.xp_reward = spec.xp
        row.is_secret = spec.secret
        row.is_active = True
        row.sort_order = (order + 1) * 10
        counts["achievements"] += 1

    quests = {row.code: row for row in (await session.scalars(select(Quest))).all()}
    for spec in QUESTS:
        row = quests.get(spec.code)
        if row is None:
            row = Quest(code=spec.code, kind=QuestKind(spec.kind))
            session.add(row)
            counts["created"] += 1
        row.user_id = None
        row.kind = QuestKind(spec.kind)
        row.title_ru, row.title_en = spec.title_ru, spec.title_en
        row.description_ru, row.description_en = spec.description_ru, spec.description_en
        row.criteria = spec.criteria()
        row.target_value = Decimal(spec.gte if spec.gte is not None else 1)
        row.xp_reward = spec.xp
        row.icon = spec.icon
        row.is_active = True
        counts["quests"] += 1

    await session.flush()
    return counts


_seeded = False


async def ensure_seeded(session: AsyncSession) -> None:
    """Gamification cannot work without its reference rows: seed them on first use
    when they are missing. Three counts until the rows are seen committed; nothing
    afterwards (per process)."""
    global _seeded
    if _seeded:
        return
    if await _complete(session):
        _seeded = True
        return
    await session.execute(select(func.pg_advisory_xact_lock(SEED_LOCK_KEY)))
    if not await _complete(session):  # another request may have seeded while we waited
        counts = await seed_gamification(session)
        log.info("gamification_seeded", **counts)
    # the flag is set by a later call, once these rows are committed and visible


async def _complete(session: AsyncSession) -> bool:
    achievements = await session.scalar(
        select(func.count()).select_from(Achievement).where(Achievement.code.in_([a.code for a in ACHIEVEMENTS]))
    )
    quests = await session.scalar(
        select(func.count()).select_from(Quest).where(Quest.code.in_([q.code for q in QUESTS]))
    )
    levels = await session.scalar(select(func.count()).select_from(Level))
    return achievements == len(ACHIEVEMENTS) and quests == len(QUESTS) and (levels or 0) >= len(LEVELS)


def reset_seed_cache() -> None:
    """Tests truncate every table between tests; they call this to re-check."""
    global _seeded
    _seeded = False


SEED = Seed(
    name="gamification",
    description=f"Levels ({len(LEVELS)}), achievements ({len(ACHIEVEMENTS)}) and quests ({len(QUESTS)}), ru+en",
    run=seed_gamification,
    tags=("gamification", "stage-8"),
)
