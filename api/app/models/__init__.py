"""SQLAlchemy models — imported here so Alembic autogenerate sees everything.

Stage mapping (all tables exist from stage 1 on purpose, so later stages only
add endpoints, never migrations for *these* entities):

* stage 1 — users, settings, devices, pairing codes, api tokens, auth events
* stage 2 — foods, favorites, recents, recipes, meal logs, water
* stage 3 — ai_jobs, ai_usage_daily
* stage 4 — exercises, templates, weekly plan, workout logs/sets, PRs
* stage 5 — body metrics, sleep logs, health_daily
* stage 6 — areas, projects, resources, tags, tasks, time entries, journal, reminders
* stage 7 — push subscriptions, notifications log
* stage 8 — xp events, levels, achievements, user achievements, streaks,
            daily scores, quests
* stage 9 — integrations, integration links (migration 0004, the optional Notion sync)
"""

from app.models.base import Base
from app.models.gamification import (
    Achievement,
    AchievementRarity,
    DailyScore,
    Level,
    Quest,
    QuestKind,
    QuestStatus,
    Streak,
    StreakKind,
    UserAchievement,
    UserQuest,
    XpEvent,
    XpKind,
)
from app.models.integrations import (
    ENTITY_TYPES,
    Integration,
    IntegrationLink,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.nutrition import (
    Food,
    FoodFavorite,
    FoodRecent,
    FoodSource,
    ItemSource,
    MatchMethod,
    MealItem,
    MealLog,
    MealSource,
    MealType,
    Recipe,
    RecipeItem,
    SavedMealTemplate,
    WaterLog,
)
from app.models.planner import (
    Area,
    JournalEntry,
    ParaKind,
    Project,
    ProjectStatus,
    RecurrenceMode,
    Reminder,
    ReminderKind,
    ReminderStatus,
    Resource,
    Tag,
    Task,
    TaskStatus,
    TimeEntry,
    TimeEntrySource,
    task_tags,
)
from app.models.system import (
    AiJob,
    AiJobKind,
    AiJobStatus,
    AiUsageDaily,
    AppMeta,
    ImportJob,
    NotificationLog,
    PushSubscription,
)
from app.models.training import (
    BodyMetric,
    Equipment,
    Exercise,
    ExerciseType,
    HealthDaily,
    HealthSource,
    KcalMethod,
    MuscleGroup,
    PersonalRecord,
    SleepLog,
    WeeklyPlan,
    WorkoutLog,
    WorkoutSet,
    WorkoutStatus,
    WorkoutTemplate,
    WorkoutTemplateItem,
)
from app.models.user import (
    ACTIVITY_FACTORS,
    ActivityLevel,
    ApiToken,
    AuthEvent,
    BmrFormula,
    Device,
    GoalMode,
    LanguageCode,
    PairingCode,
    Sex,
    User,
    UserSettings,
    UserStatus,
)

__all__ = [
    # base
    "Base",
    # identity
    "User",
    "UserSettings",
    "Device",
    "PairingCode",
    "ApiToken",
    "AuthEvent",
    "LanguageCode",
    "UserStatus",
    "Sex",
    "ActivityLevel",
    "ACTIVITY_FACTORS",
    "GoalMode",
    "BmrFormula",
    # nutrition
    "Food",
    "FoodFavorite",
    "FoodRecent",
    "FoodSource",
    "Recipe",
    "RecipeItem",
    "SavedMealTemplate",
    "MealLog",
    "MealItem",
    "MealType",
    "MealSource",
    "ItemSource",
    "MatchMethod",
    "WaterLog",
    # training
    "Exercise",
    "ExerciseType",
    "MuscleGroup",
    "Equipment",
    "WorkoutTemplate",
    "WorkoutTemplateItem",
    "WeeklyPlan",
    "WorkoutLog",
    "WorkoutSet",
    "WorkoutStatus",
    "KcalMethod",
    "PersonalRecord",
    "BodyMetric",
    "SleepLog",
    "HealthDaily",
    "HealthSource",
    # planner
    "Area",
    "Project",
    "ProjectStatus",
    "ParaKind",
    "Resource",
    "Tag",
    "Task",
    "TaskStatus",
    "RecurrenceMode",
    "TimeEntry",
    "TimeEntrySource",
    "JournalEntry",
    "Reminder",
    "ReminderKind",
    "ReminderStatus",
    "task_tags",
    # gamification
    "XpEvent",
    "XpKind",
    "Level",
    "Achievement",
    "AchievementRarity",
    "UserAchievement",
    "Streak",
    "StreakKind",
    "DailyScore",
    "Quest",
    "QuestKind",
    "QuestStatus",
    "UserQuest",
    # integrations (stage 9)
    "ENTITY_TYPES",
    "Integration",
    "IntegrationLink",
    "IntegrationProvider",
    "IntegrationStatus",
    # system
    "PushSubscription",
    "NotificationLog",
    "AiJob",
    "AiJobKind",
    "AiJobStatus",
    "AiUsageDaily",
    "ImportJob",
    "AppMeta",
]
