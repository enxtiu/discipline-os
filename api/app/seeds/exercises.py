"""
Exercise library seed (data consumed by the training screens of stage 4).

A deliberately compact but genuinely useful base: compound lifts, machines,
bodyweight work, cardio machines and a few mobility entries — with Russian and
English names, aliases (the words people actually type: «жим лёжа», «bench»),
a MET value for the burned-calorie estimate and the muscle groups involved.

Idempotency: rows are keyed by ``(name_en)`` for global entries, so re-running
the seed updates the row in place (MET tweaks, new aliases, extra instructions)
and never duplicates. ``search_vector_*`` is filled by the database trigger
``trg_exercises_search`` on insert/update — the seed does not touch it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.training import Equipment, Exercise, ExerciseType, MuscleGroup
from app.seeds import Seed

log = get_logger(__name__)


@dataclass(frozen=True)
class Row:
    name_en: str
    name_ru: str
    muscle_group: MuscleGroup
    equipment: Equipment
    met: float
    exercise_type: ExerciseType = ExerciseType.STRENGTH
    secondary: tuple[MuscleGroup, ...] = ()
    aliases: tuple[str, ...] = ()
    unilateral: bool = False
    timed: bool = False
    difficulty: int | None = None
    instructions_md: str | None = None


# MET values follow the Compendium of Physical Activities (2011), with the
# "resistance training, multiple exercises, 8-15 reps" bands for lifting.
ROWS: tuple[Row, ...] = (
    # ---------------------------------------------------------------- chest --
    Row(
        name_en="Barbell bench press",
        name_ru="Жим штанги лёжа",
        muscle_group=MuscleGroup.CHEST,
        equipment=Equipment.BARBELL,
        met=6.0,
        secondary=(MuscleGroup.TRICEPS, MuscleGroup.SHOULDERS),
        aliases=("bench press", "bench", "жим лёжа", "жим лёжа штанга"),
        difficulty=3,
    ),
    Row(
        name_en="Incline dumbbell press",
        name_ru="Жим гантелей на наклонной",
        muscle_group=MuscleGroup.CHEST,
        equipment=Equipment.DUMBBELL,
        met=5.5,
        secondary=(MuscleGroup.SHOULDERS, MuscleGroup.TRICEPS),
        aliases=("incline press", "жим гантелей вверх"),
        difficulty=3,
    ),
    Row(
        name_en="Dumbbell fly",
        name_ru="Разводка гантелей лёжа",
        muscle_group=MuscleGroup.CHEST,
        equipment=Equipment.DUMBBELL,
        met=5.0,
        secondary=(MuscleGroup.SHOULDERS,),
        aliases=("fly", "разводка", "flies"),
        difficulty=2,
    ),
    Row(
        name_en="Cable crossover",
        name_ru="Кроссовер на блоках",
        muscle_group=MuscleGroup.CHEST,
        equipment=Equipment.CABLE,
        met=5.0,
        secondary=(MuscleGroup.SHOULDERS,),
        aliases=("crossover", "кроссовер", "bлока кроссовер"),
        difficulty=2,
    ),
    Row(
        name_en="Push-up",
        name_ru="Отжимания от пола",
        muscle_group=MuscleGroup.CHEST,
        equipment=Equipment.BODYWEIGHT,
        met=5.0,
        secondary=(MuscleGroup.TRICEPS, MuscleGroup.ABS),
        aliases=("pushups", "push ups", "отжимания", "отжимание"),
        difficulty=2,
    ),
    Row(
        name_en="Dips",
        name_ru="Брусья",
        muscle_group=MuscleGroup.CHEST,
        equipment=Equipment.BODYWEIGHT,
        met=6.0,
        secondary=(MuscleGroup.TRICEPS,),
        aliases=("parallel bar dips", "брусья отжимания", "дип"),
        difficulty=3,
    ),
    # ----------------------------------------------------------------- back --
    Row(
        name_en="Deadlift",
        name_ru="Становая тяга",
        muscle_group=MuscleGroup.BACK,
        equipment=Equipment.BARBELL,
        met=6.5,
        secondary=(
            MuscleGroup.GLUTES,
            MuscleGroup.HAMSTRINGS,
            MuscleGroup.LOWER_BACK,
            MuscleGroup.TRAPS,
        ),
        aliases=("dead lift", "становая", "тяга с пола"),
        difficulty=4,
    ),
    Row(
        name_en="Pull-up",
        name_ru="Подтягивания",
        muscle_group=MuscleGroup.LATS,
        equipment=Equipment.BODYWEIGHT,
        met=6.0,
        secondary=(MuscleGroup.BICEPS, MuscleGroup.BACK),
        aliases=("pullup", "pull ups", "подтягивания", "турник"),
        difficulty=3,
    ),
    Row(
        name_en="Bent-over barbell row",
        name_ru="Тяга штанги в наклоне",
        muscle_group=MuscleGroup.BACK,
        equipment=Equipment.BARBELL,
        met=6.0,
        secondary=(MuscleGroup.LATS, MuscleGroup.BICEPS),
        aliases=("barbell row", "тяга в наклоне", "тяга штанги"),
        difficulty=3,
    ),
    Row(
        name_en="Lat pulldown",
        name_ru="Тяга верхнего блока",
        muscle_group=MuscleGroup.LATS,
        equipment=Equipment.CABLE,
        met=5.0,
        secondary=(MuscleGroup.BICEPS,),
        aliases=("pulldown", "верхний блок", "тяга блока сверху"),
        difficulty=2,
    ),
    Row(
        name_en="Seated cable row",
        name_ru="Тяга нижнего блока сидя",
        muscle_group=MuscleGroup.BACK,
        equipment=Equipment.CABLE,
        met=5.0,
        secondary=(MuscleGroup.BICEPS, MuscleGroup.LATS),
        aliases=("cable row", "нижний блок", "тяга блока сидя"),
        difficulty=2,
    ),
    Row(
        name_en="Dumbbell row",
        name_ru="Тяга гантели в наклоне",
        muscle_group=MuscleGroup.BACK,
        equipment=Equipment.DUMBBELL,
        met=5.5,
        secondary=(MuscleGroup.LATS, MuscleGroup.BICEPS),
        aliases=("one arm row", "тяга гантели одной рукой"),
        unilateral=True,
        difficulty=2,
    ),
    Row(
        name_en="Hyperextension",
        name_ru="Гиперэкстензия",
        muscle_group=MuscleGroup.LOWER_BACK,
        equipment=Equipment.BODYWEIGHT,
        met=4.0,
        secondary=(MuscleGroup.GLUTES, MuscleGroup.HAMSTRINGS),
        aliases=("back extension", "гиперэкстензия", "римский стул"),
        difficulty=1,
    ),
    Row(
        name_en="Shrug",
        name_ru="Шраги с гантелями",
        muscle_group=MuscleGroup.TRAPS,
        equipment=Equipment.DUMBBELL,
        met=4.5,
        aliases=("shrugs", "шраги", "трапеции"),
        difficulty=1,
    ),
    # ------------------------------------------------------------ shoulders --
    Row(
        name_en="Overhead press",
        name_ru="Жим штанги стоя",
        muscle_group=MuscleGroup.SHOULDERS,
        equipment=Equipment.BARBELL,
        met=6.0,
        secondary=(MuscleGroup.TRICEPS, MuscleGroup.ABS),
        aliases=("ohp", "military press", "жим стоя", "жим над головой"),
        difficulty=3,
    ),
    Row(
        name_en="Dumbbell lateral raise",
        name_ru="Махи гантелями в стороны",
        muscle_group=MuscleGroup.SHOULDERS,
        equipment=Equipment.DUMBBELL,
        met=4.5,
        aliases=("lateral raise", "махи в стороны", "разводка рук"),
        difficulty=1,
    ),
    Row(
        name_en="Face pull",
        name_ru="Фейс-пул на блоке",
        muscle_group=MuscleGroup.SHOULDERS,
        equipment=Equipment.CABLE,
        met=4.5,
        secondary=(MuscleGroup.TRAPS,),
        aliases=("facepull", "фейс пул", "тяга к лицу"),
        difficulty=2,
    ),
    # ------------------------------------------------------ arms & forearms --
    Row(
        name_en="Barbell curl",
        name_ru="Сгибания на бицепс со штангой",
        muscle_group=MuscleGroup.BICEPS,
        equipment=Equipment.BARBELL,
        met=4.5,
        secondary=(MuscleGroup.FOREARMS,),
        aliases=("biceps curl", "бицепс штанга", "подъём штанги на бицепс"),
        difficulty=1,
    ),
    Row(
        name_en="Dumbbell hammer curl",
        name_ru="Молотковые сгибания с гантелями",
        muscle_group=MuscleGroup.BICEPS,
        equipment=Equipment.DUMBBELL,
        met=4.5,
        secondary=(MuscleGroup.FOREARMS,),
        aliases=("hammer curl", "молоток", "молотковые сгибания"),
        difficulty=1,
    ),
    Row(
        name_en="Triceps pushdown",
        name_ru="Разгибания на трицепс на блоке",
        muscle_group=MuscleGroup.TRICEPS,
        equipment=Equipment.CABLE,
        met=4.5,
        aliases=("pushdown", "трицепс блок", "разгибания на блоке"),
        difficulty=1,
    ),
    Row(
        name_en="Skull crusher",
        name_ru="Французский жим лёжа",
        muscle_group=MuscleGroup.TRICEPS,
        equipment=Equipment.BARBELL,
        met=5.0,
        aliases=("lying triceps extension", "французский жим", "трицепс лёжа"),
        difficulty=2,
    ),
    Row(
        name_en="Wrist curl",
        name_ru="Сгибания кистей",
        muscle_group=MuscleGroup.FOREARMS,
        equipment=Equipment.DUMBBELL,
        met=3.5,
        aliases=("forearm curl", "кисти", "предплечья"),
        difficulty=1,
    ),
    # ------------------------------------------------------------------ abs --
    Row(
        name_en="Plank",
        name_ru="Планка",
        muscle_group=MuscleGroup.ABS,
        equipment=Equipment.BODYWEIGHT,
        met=3.5,
        secondary=(MuscleGroup.OBLIQUES, MuscleGroup.LOWER_BACK),
        aliases=("front plank", "планка", "упор лёжа"),
        timed=True,
        difficulty=1,
    ),
    Row(
        name_en="Side plank",
        name_ru="Планка на боку",
        muscle_group=MuscleGroup.OBLIQUES,
        equipment=Equipment.BODYWEIGHT,
        met=3.5,
        aliases=("боковая планка",),
        unilateral=True,
        timed=True,
        difficulty=2,
    ),
    Row(
        name_en="Crunch",
        name_ru="Скручивания",
        muscle_group=MuscleGroup.ABS,
        equipment=Equipment.BODYWEIGHT,
        met=3.8,
        aliases=("crunches", "скручивания", "качание пресса"),
        difficulty=1,
    ),
    Row(
        name_en="Hanging leg raise",
        name_ru="Подъём ног в висе",
        muscle_group=MuscleGroup.ABS,
        equipment=Equipment.BODYWEIGHT,
        met=4.0,
        secondary=(MuscleGroup.FOREARMS,),
        aliases=("leg raise", "подъём ног", "подъём колен в висе"),
        difficulty=3,
    ),
    Row(
        name_en="Russian twist",
        name_ru="Русские скручивания",
        muscle_group=MuscleGroup.OBLIQUES,
        equipment=Equipment.BODYWEIGHT,
        met=4.0,
        aliases=("russian twists", "русский твист"),
        difficulty=2,
    ),
    # ------------------------------------------------------- legs & glutes --
    Row(
        name_en="Back squat",
        name_ru="Приседания со штангой",
        muscle_group=MuscleGroup.QUADS,
        equipment=Equipment.BARBELL,
        met=6.5,
        secondary=(MuscleGroup.GLUTES, MuscleGroup.HAMSTRINGS, MuscleGroup.ABS),
        aliases=("squat", "приседания", "присед"),
        difficulty=4,
    ),
    Row(
        name_en="Front squat",
        name_ru="Фронтальные приседания",
        muscle_group=MuscleGroup.QUADS,
        equipment=Equipment.BARBELL,
        met=6.5,
        secondary=(MuscleGroup.GLUTES, MuscleGroup.ABS),
        aliases=("front squat", "фронтальный присед"),
        difficulty=4,
    ),
    Row(
        name_en="Romanian deadlift",
        name_ru="Румынская тяга",
        muscle_group=MuscleGroup.HAMSTRINGS,
        equipment=Equipment.BARBELL,
        met=6.0,
        secondary=(MuscleGroup.GLUTES, MuscleGroup.LOWER_BACK),
        aliases=("rdl", "румынская", "тяга на прямых ногах"),
        difficulty=3,
    ),
    Row(
        name_en="Leg press",
        name_ru="Жим ногами",
        muscle_group=MuscleGroup.QUADS,
        equipment=Equipment.MACHINE,
        met=5.5,
        secondary=(MuscleGroup.GLUTES,),
        aliases=("жим ногами", "платформа"),
        difficulty=2,
    ),
    Row(
        name_en="Bulgarian split squat",
        name_ru="Болгарский выпад",
        muscle_group=MuscleGroup.QUADS,
        equipment=Equipment.DUMBBELL,
        met=6.0,
        secondary=(MuscleGroup.GLUTES,),
        aliases=("split squat", "болгарский", "болгарские выпады"),
        unilateral=True,
        difficulty=3,
    ),
    Row(
        name_en="Lunge",
        name_ru="Выпады",
        muscle_group=MuscleGroup.QUADS,
        equipment=Equipment.DUMBBELL,
        met=6.0,
        secondary=(MuscleGroup.GLUTES, MuscleGroup.HAMSTRINGS),
        aliases=("lunges", "выпады", "шаги выпадами"),
        unilateral=True,
        difficulty=2,
    ),
    Row(
        name_en="Hip thrust",
        name_ru="Ягодичный мост со штангой",
        muscle_group=MuscleGroup.GLUTES,
        equipment=Equipment.BARBELL,
        met=5.5,
        secondary=(MuscleGroup.HAMSTRINGS,),
        aliases=("glute bridge", "ягодичный мост", "хип-траст"),
        difficulty=2,
    ),
    Row(
        name_en="Leg curl",
        name_ru="Сгибания ног в тренажёре",
        muscle_group=MuscleGroup.HAMSTRINGS,
        equipment=Equipment.MACHINE,
        met=4.5,
        aliases=("leg curls", "сгибания ног лёжа"),
        difficulty=1,
    ),
    Row(
        name_en="Leg extension",
        name_ru="Разгибания ног в тренажёре",
        muscle_group=MuscleGroup.QUADS,
        equipment=Equipment.MACHINE,
        met=4.5,
        aliases=("leg extensions", "разгибания ног сидя"),
        difficulty=1,
    ),
    Row(
        name_en="Standing calf raise",
        name_ru="Подъём на носки стоя",
        muscle_group=MuscleGroup.CALVES,
        equipment=Equipment.MACHINE,
        met=4.0,
        aliases=("calf raise", "носки", "икры"),
        difficulty=1,
    ),
    Row(
        name_en="Kettlebell swing",
        name_ru="Махи гирей",
        muscle_group=MuscleGroup.GLUTES,
        equipment=Equipment.KETTLEBELL,
        met=8.0,
        secondary=(MuscleGroup.HAMSTRINGS, MuscleGroup.LOWER_BACK, MuscleGroup.CARDIO),
        aliases=("kb swing", "махи гирей", "свинг"),
        difficulty=2,
    ),
    # --------------------------------------------------------------- cardio --
    Row(
        name_en="Treadmill running",
        name_ru="Бег на дорожке",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.CARDIO_MACHINE,
        met=9.8,
        exercise_type=ExerciseType.CARDIO,
        secondary=(MuscleGroup.QUADS, MuscleGroup.CALVES),
        aliases=("treadmill", "бег", "дорожка"),
        timed=True,
    ),
    Row(
        name_en="Outdoor running",
        name_ru="Бег на улице",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.OTHER,
        met=9.8,
        exercise_type=ExerciseType.CARDIO,
        secondary=(MuscleGroup.QUADS, MuscleGroup.CALVES),
        aliases=("run", "бег улица", "кросс"),
        timed=True,
    ),
    Row(
        name_en="Walking",
        name_ru="Ходьба",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.OTHER,
        met=3.5,
        exercise_type=ExerciseType.CARDIO,
        aliases=("walk", "прогулка", "ходьба"),
        timed=True,
    ),
    Row(
        name_en="Stationary bike",
        name_ru="Велотренажёр",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.CARDIO_MACHINE,
        met=7.0,
        exercise_type=ExerciseType.CARDIO,
        secondary=(MuscleGroup.QUADS,),
        aliases=("bike", "cycling", "велотренажёр", "велосипед"),
        timed=True,
    ),
    Row(
        name_en="Rowing machine",
        name_ru="Гребной тренажёр",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.CARDIO_MACHINE,
        met=8.5,
        exercise_type=ExerciseType.CARDIO,
        secondary=(MuscleGroup.BACK, MuscleGroup.LATS),
        aliases=("rower", "erg", "гребля", "гребной"),
        timed=True,
    ),
    Row(
        name_en="Jump rope",
        name_ru="Скакалка",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.OTHER,
        met=11.0,
        exercise_type=ExerciseType.CARDIO,
        secondary=(MuscleGroup.CALVES,),
        aliases=("skipping", "скакалка", "прыжки со скакалкой"),
        timed=True,
    ),
    Row(
        name_en="Elliptical trainer",
        name_ru="Эллиптический тренажёр",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.CARDIO_MACHINE,
        met=5.0,
        exercise_type=ExerciseType.CARDIO,
        aliases=("elliptical", "орбитрек", "эллипс"),
        timed=True,
    ),
    Row(
        name_en="Stair climber",
        name_ru="Степпер",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.CARDIO_MACHINE,
        met=9.0,
        exercise_type=ExerciseType.CARDIO,
        secondary=(MuscleGroup.GLUTES, MuscleGroup.CALVES),
        aliases=("stepmill", "степпер", "лестница"),
        timed=True,
    ),
    Row(
        name_en="Swimming",
        name_ru="Плавание",
        muscle_group=MuscleGroup.CARDIO,
        equipment=Equipment.OTHER,
        met=8.3,
        exercise_type=ExerciseType.SPORT,
        secondary=(MuscleGroup.LATS, MuscleGroup.SHOULDERS),
        aliases=("swim", "плавание", "бассейн"),
        timed=True,
    ),
    Row(
        name_en="Burpee",
        name_ru="Бёрпи",
        muscle_group=MuscleGroup.FULL_BODY,
        equipment=Equipment.BODYWEIGHT,
        met=8.0,
        exercise_type=ExerciseType.CARDIO,
        secondary=(MuscleGroup.CHEST, MuscleGroup.QUADS, MuscleGroup.ABS),
        aliases=("burpees", "бёрпи", "берпи"),
        difficulty=3,
    ),
    # -------------------------------------------------------------- mobility --
    Row(
        name_en="Cat-cow stretch",
        name_ru="Кошка-корова",
        muscle_group=MuscleGroup.LOWER_BACK,
        equipment=Equipment.OTHER,
        met=2.3,
        exercise_type=ExerciseType.MOBILITY,
        aliases=("cat cow", "кошка корова", "разминка спины"),
        timed=True,
        difficulty=1,
    ),
    Row(
        name_en="World's greatest stretch",
        name_ru="Лучшая растяжка в мире",
        muscle_group=MuscleGroup.FULL_BODY,
        equipment=Equipment.OTHER,
        met=2.5,
        exercise_type=ExerciseType.MOBILITY,
        aliases=("wgs", "растяжка всего тела"),
        timed=True,
        difficulty=2,
    ),
    Row(
        name_en="Hip flexor stretch",
        name_ru="Растяжка сгибателей бедра",
        muscle_group=MuscleGroup.QUADS,
        equipment=Equipment.OTHER,
        met=2.3,
        exercise_type=ExerciseType.MOBILITY,
        aliases=("hip stretch", "растяжка подвздошной"),
        unilateral=True,
        timed=True,
        difficulty=1,
    ),
    Row(
        name_en="Thoracic foam roll",
        name_ru="МФР грудного отдела",
        muscle_group=MuscleGroup.BACK,
        equipment=Equipment.OTHER,
        met=2.3,
        exercise_type=ExerciseType.MOBILITY,
        aliases=("foam roll", "мфр", "миофасциальный релиз"),
        timed=True,
        difficulty=1,
    ),
)


async def seed_exercises(session) -> dict[str, int]:
    """Upsert the global exercise library. Returns ``{created, updated}``."""
    created = updated = 0

    existing = await session.execute(
        select(Exercise).where(
            Exercise.is_global.is_(True),
            Exercise.user_id.is_(None),
        )
    )
    by_name = {row.name_en: row for row in existing.scalars().all() if row.name_en}

    for row in ROWS:
        exercise = by_name.get(row.name_en)
        if exercise is None:
            exercise = Exercise(name_en=row.name_en, is_global=True, source="seed")
            session.add(exercise)
            created += 1
        else:
            updated += 1

        exercise.name_ru = row.name_ru
        exercise.aliases = list(row.aliases)
        # The search trigger concatenates these columns; keep it in sync here so
        # the seed does not depend on the trigger firing per column.
        exercise.aliases_text = " ".join(row.aliases)
        exercise.muscle_group = row.muscle_group
        exercise.secondary_muscles = [m.value for m in row.secondary]
        exercise.equipment = row.equipment
        exercise.exercise_type = row.exercise_type
        exercise.met = row.met
        exercise.is_unilateral = row.unilateral
        exercise.is_timed = row.timed
        exercise.difficulty = row.difficulty
        exercise.source = "seed"

    await session.flush()
    return {"created": created, "updated": updated, "total": len(ROWS)}


SEED = Seed(
    name="exercises",
    description=f"Global exercise library ({len(ROWS)} exercises, ru+en, with MET values)",
    run=seed_exercises,
    tags=("training", "stage-4"),
)


def main() -> None:  # pragma: no cover - thin wrapper for `python -m app.seeds.exercises`
    """Run just this seed: ``python -m app.seeds.exercises``."""
    import asyncio

    from app.db import SessionLocal

    async def _run() -> None:
        async with SessionLocal() as session:
            counts = await seed_exercises(session)
            await session.commit()
            log.info("seed_finished", seed="exercises", **counts)

    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    main()
