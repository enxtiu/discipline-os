"""Server-side labels for enum values.

The frontend has its own translation files; this list covers values that live
in the database (statuses, muscle groups, meal types…). Keeping them here means
a new enum member only needs one place to get both languages, and the mini-app
can render lists without guessing keys.
"""

from __future__ import annotations

from typing import Any

I18N_ENUM_LABELS: list[dict[str, str]] = [
    # ---- language / units -------------------------------------------------
    {"code": "units.kg", "ru": "кг", "en": "kg"},
    {"code": "units.cm", "ru": "см", "en": "cm"},
    {"code": "units.kcal", "ru": "ккал", "en": "kcal"},
    {"code": "units.g", "ru": "г", "en": "g"},
    {"code": "units.ml", "ru": "мл", "en": "ml"},
    {"code": "units.steps", "ru": "шаги", "en": "steps"},
    # ---- sex / activity ---------------------------------------------------
    {"code": "sex.male", "ru": "Мужской", "en": "Male"},
    {"code": "sex.female", "ru": "Женский", "en": "Female"},
    {"code": "sex.other", "ru": "Не указан", "en": "Not specified"},
    {"code": "activity.sedentary", "ru": "Сидячий", "en": "Sedentary"},
    {"code": "activity.light", "ru": "Лёгкая активность", "en": "Light activity"},
    {"code": "activity.moderate", "ru": "Средняя активность", "en": "Moderate activity"},
    {"code": "activity.active", "ru": "Высокая активность", "en": "Active"},
    {"code": "activity.very_active", "ru": "Очень высокая", "en": "Very active"},
    # ---- goals ------------------------------------------------------------
    {"code": "goal.lose", "ru": "Похудение", "en": "Lose"},
    {"code": "goal.maintain", "ru": "Поддержание", "en": "Maintain"},
    {"code": "goal.gain", "ru": "Набор массы", "en": "Gain"},
    {"code": "goal.formula.auto", "ru": "Авто", "en": "Auto"},
    {"code": "goal.formula.mifflin", "ru": "Миффлин-Сан Жеор", "en": "Mifflin-St Jeor"},
    {"code": "goal.formula.katch", "ru": "Катч-МакАрдл", "en": "Katch-McArdle"},
    # ---- target warnings --------------------------------------------------
    {
        "code": "warning.below_safe_floor",
        "ru": "Ниже безопасного минимума — цель поднята",
        "en": "Below the safe floor — target raised",
    },
    {"code": "warning.aggressive_deficit", "ru": "Слишком агрессивный дефицит", "en": "Aggressive deficit"},
    {"code": "warning.high_deficit", "ru": "Высокий дефицит (20%+)", "en": "High deficit (20%+)"},
    {"code": "warning.aggressive_surplus", "ru": "Слишком большой профицит", "en": "Aggressive surplus"},
    {"code": "warning.low_body_fat_deficit", "ru": "Низкий процент жира + дефицит", "en": "Low body fat + deficit"},
    {
        "code": "warning.missing_body_params",
        "ru": "Не хватает данных: вес, рост, возраст",
        "en": "Missing body data: weight, height, age",
    },
    {
        "code": "warning.macros_exceed_kcal",
        "ru": "Белки и жиры превышают калорийность",
        "en": "Protein and fat exceed the calorie target",
    },
    {
        "code": "warning.katch_requires_body_fat",
        "ru": "Для формулы Катча нужен % жира",
        "en": "Katch-McArdle needs body fat %",
    },
    {
        "code": "warning.below_safe_floor_confirmed",
        "ru": "Подтверждено вручную: цель ниже минимума",
        "en": "Manually confirmed: target below the floor",
    },
    # ---- meal types -------------------------------------------------------
    {"code": "meal.breakfast", "ru": "Завтрак", "en": "Breakfast"},
    {"code": "meal.lunch", "ru": "Обед", "en": "Lunch"},
    {"code": "meal.dinner", "ru": "Ужин", "en": "Dinner"},
    {"code": "meal.snack", "ru": "Перекус", "en": "Snack"},
    {"code": "meal.other", "ru": "Другое", "en": "Other"},
    # ---- task status / priority -------------------------------------------
    {"code": "task.status.inbox", "ru": "Входящие", "en": "Inbox"},
    {"code": "task.status.open", "ru": "Открыта", "en": "Open"},
    {"code": "task.status.scheduled", "ru": "Запланирована", "en": "Scheduled"},
    {"code": "task.status.in_progress", "ru": "В работе", "en": "In progress"},
    {"code": "task.status.waiting", "ru": "Ждём", "en": "Waiting"},
    {"code": "task.status.blocked", "ru": "Заблокирована", "en": "Blocked"},
    {"code": "task.status.done", "ru": "Готово", "en": "Done"},
    {"code": "task.status.cancelled", "ru": "Отменена", "en": "Cancelled"},
    {"code": "task.priority.p1", "ru": "P1 — критично", "en": "P1 — critical"},
    {"code": "task.priority.p2", "ru": "P2 — важно", "en": "P2 — important"},
    {"code": "task.priority.p3", "ru": "P3 — обычно", "en": "P3 — normal"},
    {"code": "task.priority.p4", "ru": "P4 — когда-нибудь", "en": "P4 — someday"},
    # ---- muscle groups ----------------------------------------------------
    {"code": "muscle.chest", "ru": "Грудь", "en": "Chest"},
    {"code": "muscle.back", "ru": "Спина", "en": "Back"},
    {"code": "muscle.lats", "ru": "Широчайшие", "en": "Lats"},
    {"code": "muscle.traps", "ru": "Трапеции", "en": "Traps"},
    {"code": "muscle.shoulders", "ru": "Плечи", "en": "Shoulders"},
    {"code": "muscle.biceps", "ru": "Бицепс", "en": "Biceps"},
    {"code": "muscle.triceps", "ru": "Трицепс", "en": "Triceps"},
    {"code": "muscle.forearms", "ru": "Предплечья", "en": "Forearms"},
    {"code": "muscle.abs", "ru": "Пресс", "en": "Abs"},
    {"code": "muscle.obliques", "ru": "Косые мышцы", "en": "Obliques"},
    {"code": "muscle.lower_back", "ru": "Поясница", "en": "Lower back"},
    {"code": "muscle.glutes", "ru": "Ягодицы", "en": "Glutes"},
    {"code": "muscle.quads", "ru": "Квадрицепсы", "en": "Quads"},
    {"code": "muscle.hamstrings", "ru": "Бицепс бедра", "en": "Hamstrings"},
    {"code": "muscle.calves", "ru": "Икры", "en": "Calves"},
    {"code": "muscle.full_body", "ru": "Всё тело", "en": "Full body"},
    {"code": "muscle.cardio", "ru": "Кардио", "en": "Cardio"},
    {"code": "muscle.other", "ru": "Другое", "en": "Other"},
    # ---- equipment --------------------------------------------------------
    {"code": "equipment.barbell", "ru": "Штанга", "en": "Barbell"},
    {"code": "equipment.dumbbell", "ru": "Гантели", "en": "Dumbbell"},
    {"code": "equipment.kettlebell", "ru": "Гиря", "en": "Kettlebell"},
    {"code": "equipment.machine", "ru": "Тренажёр", "en": "Machine"},
    {"code": "equipment.cable", "ru": "Блок", "en": "Cable"},
    {"code": "equipment.bodyweight", "ru": "Своё тело", "en": "Bodyweight"},
    {"code": "equipment.bands", "ru": "Резинки", "en": "Bands"},
    {"code": "equipment.cardio_machine", "ru": "Кардиотренажёр", "en": "Cardio machine"},
    {"code": "equipment.other", "ru": "Другое", "en": "Other"},
    # ---- workout ----------------------------------------------------------
    {"code": "workout.strength", "ru": "Силовая", "en": "Strength"},
    {"code": "workout.cardio", "ru": "Кардио", "en": "Cardio"},
    {"code": "workout.mobility", "ru": "Мобильность", "en": "Mobility"},
    {"code": "workout.sport", "ru": "Спорт", "en": "Sport"},
    {"code": "workout.other", "ru": "Другое", "en": "Other"},
    {"code": "workout.status.planned", "ru": "Запланирована", "en": "Planned"},
    {"code": "workout.status.in_progress", "ru": "Идёт", "en": "In progress"},
    {"code": "workout.status.completed", "ru": "Завершена", "en": "Completed"},
    {"code": "workout.status.skipped", "ru": "Пропущена", "en": "Skipped"},
    # ---- PARA -------------------------------------------------------------
    {"code": "para.project", "ru": "Проект", "en": "Project"},
    {"code": "para.area", "ru": "Сфера", "en": "Area"},
    {"code": "para.resource", "ru": "Ресурс", "en": "Resource"},
    {"code": "project.status.active", "ru": "Активен", "en": "Active"},
    {"code": "project.status.on_hold", "ru": "На паузе", "en": "On hold"},
    {"code": "project.status.completed", "ru": "Завершён", "en": "Completed"},
    {"code": "project.status.cancelled", "ru": "Отменён", "en": "Cancelled"},
    {"code": "project.status.archived", "ru": "В архиве", "en": "Archived"},
    # ---- streaks / gamification ------------------------------------------
    {"code": "streak.meal_logging", "ru": "Дни с дневником питания", "en": "Meal logging streak"},
    {"code": "streak.workout", "ru": "Тренировки", "en": "Workout streak"},
    {"code": "streak.water", "ru": "Вода", "en": "Water streak"},
    {"code": "streak.steps", "ru": "Шаги", "en": "Steps streak"},
    {"code": "streak.tasks", "ru": "Задачи", "en": "Tasks streak"},
    {"code": "streak.review", "ru": "Вечерний обзор", "en": "Daily review streak"},
    {"code": "streak.overall", "ru": "Общая серия", "en": "Overall streak"},
    {"code": "rarity.common", "ru": "Обычная", "en": "Common"},
    {"code": "rarity.rare", "ru": "Редкая", "en": "Rare"},
    {"code": "rarity.epic", "ru": "Эпическая", "en": "Epic"},
    {"code": "rarity.legendary", "ru": "Легендарная", "en": "Legendary"},
    # ---- notifications ----------------------------------------------------
    {"code": "notify.task_reminders", "ru": "Напоминания о задачах", "en": "Task reminders"},
    {"code": "notify.meal_logging", "ru": "Напоминания записать еду", "en": "Meal logging reminders"},
    {"code": "notify.water", "ru": "Напоминания про воду", "en": "Water reminders"},
    {"code": "notify.workout", "ru": "План тренировок", "en": "Planned workout"},
    {"code": "notify.evening_review", "ru": "Вечерний обзор дня", "en": "Evening review"},
    {"code": "notify.streak_at_risk", "ru": "Серия под угрозой", "en": "Streak at risk"},
    # ---- errors -----------------------------------------------------------
    {"code": "error.unauthorized", "ru": "Нужно подключить устройство", "en": "Device not connected"},
    {"code": "error.invalid_pairing_code", "ru": "Код неверный или истёк", "en": "Code is invalid or expired"},
    {"code": "error.pairing_code_used", "ru": "Код уже использован", "en": "Code already used"},
    {
        "code": "error.pairing_code_locked",
        "ru": "Слишком много попыток, создайте новый код",
        "en": "Too many attempts, create a new code",
    },
    {"code": "error.device_limit_reached", "ru": "Достигнут лимит устройств", "en": "Device limit reached"},
    {"code": "error.device_revoked", "ru": "Это устройство отключено", "en": "This device was revoked"},
    {"code": "error.token_revoked", "ru": "Токен отозван", "en": "Token revoked"},
    {"code": "error.token_expired", "ru": "Срок действия токена истёк", "en": "Token expired"},
    {"code": "error.forbidden", "ru": "Недостаточно прав", "en": "Not allowed"},
    {"code": "error.rate_limited", "ru": "Слишком много запросов", "en": "Too many requests"},
    {"code": "error.last_device", "ru": "Нельзя отключить последнее устройство", "en": "Cannot revoke the last device"},
    {
        "code": "error.target_below_safe_floor",
        "ru": "Цель ниже безопасного минимума",
        "en": "Target below the safe floor",
    },
    {"code": "error.offline_ai", "ru": "ИИ-функции доступны только онлайн", "en": "AI features need a connection"},
]


def as_dict() -> dict[str, dict[str, str]]:
    return {item["code"]: {"ru": item["ru"], "en": item["en"]} for item in I18N_ENUM_LABELS}


def lookup(code: str) -> dict[str, Any] | None:
    for item in I18N_ENUM_LABELS:
        if item["code"] == code:
            return item
    return None
