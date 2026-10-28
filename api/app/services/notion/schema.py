"""The Notion databases DisciplineOS creates — one per kind, in the owner's language.

Every property has a **stable internal key** (``Prop.key``); names are only labels,
chosen once (Russian or English, by the owner's language when the database is
created) and never relied upon afterwards: the property ids Notion assigns are
stored (``config.databases[kind].props``) and pages are written by id, so renaming a
column in Notion breaks nothing. Select options are written by name in the
database's language (``config.databases[kind].lang``).

Relations are two-way (``dual_property``) so Notion shows the reverse side too
(a project lists its tasks, a task its subtasks); they point at the target's
``data_source_id`` (Notion-Version ≥ 2025-09-03). A relation whose target database
does not exist yet is added later by the schema reconcile step in :mod:`.sync`.

Request shapes: https://developers.notion.com/reference/database-create (``title``,
``description``, ``icon``, ``initial_data_source.properties``),
https://developers.notion.com/reference/property-object (title, rich_text, number,
select, multi_select, date, checkbox, relation; option names: no commas, unique
case-insensitively; colors ``default gray brown orange yellow green blue purple pink red``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ENTITY_OF",
    "ID_KEY",
    "KINDS",
    "KIND_DEFS",
    "KindDef",
    "Option",
    "Prop",
    "lang_of",
    "property_config",
]

KINDS: tuple[str, ...] = ("areas", "projects", "tasks", "journal", "days")  # dependency order
ENTITY_OF: dict[str, str] = {
    "areas": "area",
    "projects": "project",
    "tasks": "task",
    "journal": "journal",
    "days": "day",
}
ID_KEY = "dsos_id"


def lang_of(language: object) -> str:
    """``ru`` (default) or ``en`` from a settings language value."""
    value = str(getattr(language, "value", language) or "ru").lower()
    return "en" if value.startswith("en") else "ru"


@dataclass(frozen=True, slots=True)
class Option:
    key: str
    ru: str
    en: str
    color: str = "default"

    def name(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru


@dataclass(frozen=True, slots=True)
class Prop:
    key: str
    type: str  # title | rich_text | number | select | multi_select | date | checkbox | relation
    ru: str
    en: str
    options: tuple[Option, ...] = ()
    target: str | None = None  # relation: the target kind
    synced_ru: str | None = None  # relation: the reverse property's name in the target
    synced_en: str | None = None
    description_ru: str | None = None
    description_en: str | None = None

    def name(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru

    def option(self, key: str, lang: str) -> str:
        for item in self.options:
            if item.key == key:
                return item.name(lang)
        raise KeyError(key)


@dataclass(frozen=True, slots=True)
class KindDef:
    kind: str
    ru: str
    en: str
    icon: str
    props: tuple[Prop, ...] = field(default_factory=tuple)

    def title(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru

    def prop(self, key: str) -> Prop:
        for item in self.props:
            if item.key == key:
                return item
        raise KeyError(key)

    @property
    def relations(self) -> tuple[Prop, ...]:
        return tuple(item for item in self.props if item.type == "relation")


DESCRIPTION = {
    "ru": "Копия данных из DisciplineOS (односторонняя): правки здесь перезапишутся при следующей синхронизации.",
    "en": "A one-way copy of DisciplineOS data: edits made here are overwritten by the next sync.",
}

ID_PROP = Prop(
    ID_KEY,
    "rich_text",
    "ID DisciplineOS",
    "DisciplineOS ID",
    description_ru="Служебный ключ записи в DisciplineOS — не меняйте.",
    description_en="The record's key in DisciplineOS — do not edit.",
)

PROJECT_STATUS = (
    Option("active", "Активный", "Active", "blue"),
    Option("on_hold", "На паузе", "On hold", "yellow"),
    Option("completed", "Завершён", "Completed", "green"),
    Option("cancelled", "Отменён", "Cancelled", "gray"),
)
TASK_STATUS = (
    Option("inbox", "Входящие", "Inbox", "gray"),
    Option("open", "Открыта", "Open", "default"),
    Option("scheduled", "Запланирована", "Scheduled", "blue"),
    Option("in_progress", "В работе", "In progress", "yellow"),
    Option("waiting", "Ожидает", "Waiting", "orange"),
    Option("blocked", "Заблокирована", "Blocked", "red"),
    Option("done", "Выполнена", "Done", "green"),
    Option("cancelled", "Отменена", "Cancelled", "brown"),
)
PRIORITY = (
    Option("1", "P1", "P1", "red"),
    Option("2", "P2", "P2", "orange"),
    Option("3", "P3", "P3", "blue"),
    Option("4", "P4", "P4", "gray"),
)

KIND_DEFS: dict[str, KindDef] = {
    "areas": KindDef(
        "areas",
        "Области",
        "Areas",
        "🧭",
        (
            Prop("name", "title", "Название", "Name"),
            Prop("description", "rich_text", "Описание", "Description"),
            Prop("weekly_target", "number", "Цель в неделю, мин", "Weekly target, min"),
            ID_PROP,
        ),
    ),
    "projects": KindDef(
        "projects",
        "Проекты",
        "Projects",
        "📁",
        (
            Prop("name", "title", "Название", "Name"),
            Prop("status", "select", "Статус", "Status", options=PROJECT_STATUS),
            Prop("area", "relation", "Область", "Area", target="areas", synced_ru="Проекты", synced_en="Projects"),
            Prop("outcome", "rich_text", "Результат", "Outcome"),
            Prop("start", "date", "Начало", "Start"),
            Prop("deadline", "date", "Дедлайн", "Deadline"),
            Prop("completed", "date", "Дата завершения", "Completed on"),
            Prop("description", "rich_text", "Описание", "Description"),
            ID_PROP,
        ),
    ),
    "tasks": KindDef(
        "tasks",
        "Задачи",
        "Tasks",
        "✅",
        (
            Prop("title", "title", "Задача", "Task"),
            Prop("status", "select", "Статус", "Status", options=TASK_STATUS),
            Prop("priority", "select", "Приоритет", "Priority", options=PRIORITY),
            Prop("project", "relation", "Проект", "Project", target="projects", synced_ru="Задачи", synced_en="Tasks"),
            Prop("area", "relation", "Область", "Area", target="areas", synced_ru="Задачи", synced_en="Tasks"),
            Prop(
                "parent",
                "relation",
                "Родительская задача",
                "Parent task",
                target="tasks",
                synced_ru="Подзадачи",
                synced_en="Subtasks",
            ),
            Prop("due", "date", "Срок", "Due"),
            Prop("block", "date", "Блок времени", "Time block"),
            Prop("estimate", "number", "Оценка, мин", "Estimate, min"),
            Prop("tracked", "number", "Затрачено, мин", "Tracked, min"),
            Prop("tags", "multi_select", "Теги", "Tags"),
            Prop("context", "rich_text", "Контекст", "Context"),
            Prop("recurring", "checkbox", "Повторяется", "Repeats"),
            Prop("completed", "date", "Дата выполнения", "Completed on"),
            Prop("created", "date", "Создана", "Created"),
            Prop("notes", "rich_text", "Заметки", "Notes"),
            ID_PROP,
        ),
    ),
    "journal": KindDef(
        "journal",
        "Дневник",
        "Journal",
        "📓",
        (
            Prop("day", "title", "День", "Day"),
            Prop("date", "date", "Дата", "Date"),
            Prop("rating", "number", "Оценка дня", "Day rating"),
            Prop("mood", "number", "Настроение", "Mood"),
            Prop("energy", "number", "Энергия", "Energy"),
            Prop("wins", "rich_text", "Победы", "Wins"),
            Prop("improvements", "rich_text", "Что улучшить", "To improve"),
            Prop("notes", "rich_text", "Заметки", "Notes"),
            Prop("tags", "multi_select", "Теги", "Tags"),
            ID_PROP,
        ),
    ),
    "days": KindDef(
        "days",
        "Итоги дня",
        "Daily summary",
        "📊",
        (
            Prop("day", "title", "День", "Day"),
            Prop("date", "date", "Дата", "Date"),
            Prop("kcal", "number", "Съедено, ккал", "Eaten, kcal"),
            Prop("kcal_target", "number", "Цель, ккал", "Target, kcal"),
            Prop("in_zone", "checkbox", "В зоне цели", "In target zone"),
            Prop("protein", "number", "Белок, г", "Protein, g"),
            Prop("water", "number", "Вода, мл", "Water, ml"),
            Prop("steps", "number", "Шаги", "Steps"),
            Prop("sleep", "number", "Сон, ч", "Sleep, h"),
            Prop("workouts", "number", "Тренировки", "Workouts"),
            # burned calories: their own columns, never part of "eaten" or the target
            Prop("workout_kcal", "number", "Сожжено на тренировках, ккал", "Burned in workouts, kcal"),
            Prop("active_kcal", "number", "Активная энергия (Health), ккал", "Active energy (Health), kcal"),
            Prop("tracked", "number", "Учтённое время, мин", "Tracked time, min"),
            Prop("tasks_done", "number", "Задач выполнено", "Tasks done"),
            Prop("score", "number", "Баллы дня", "Day score"),
            ID_PROP,
        ),
    ),
}


def property_config(prop: Prop, lang: str, *, target_data_source: str | None = None) -> dict[str, Any] | None:
    """The schema of one property for create-database / update-data-source. A relation
    needs its target's data source id (``None`` → the property cannot exist yet)."""
    config: dict[str, Any]
    if prop.type == "title":
        config = {"type": "title", "title": {}}
    elif prop.type == "rich_text":
        config = {"type": "rich_text", "rich_text": {}}
    elif prop.type == "number":
        config = {"type": "number", "number": {"format": "number"}}
    elif prop.type in ("select", "multi_select"):
        options = [{"name": option.name(lang), "color": option.color} for option in prop.options]
        config = {"type": prop.type, prop.type: {"options": options}}
    elif prop.type == "date":
        config = {"type": "date", "date": {}}
    elif prop.type == "checkbox":
        config = {"type": "checkbox", "checkbox": {}}
    elif prop.type == "relation":
        if not target_data_source:
            return None
        synced = prop.synced_en if lang == "en" else prop.synced_ru
        dual: dict[str, Any] = {"synced_property_name": synced} if synced else {}
        config = {
            "type": "relation",
            "relation": {"data_source_id": target_data_source, "type": "dual_property", "dual_property": dual},
        }
    else:  # pragma: no cover - the definitions above use only these types
        raise ValueError(prop.type)
    description = prop.description_en if lang == "en" else prop.description_ru
    if description:
        config["description"] = description
    return config
