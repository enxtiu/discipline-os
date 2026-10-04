"""AI meal logging: photo/text → reviewed suggestions → (user saves) meal.

The AI is a fallback, not the source of truth (roadmap rule):

* every recognised item is first **resolved against the local food library**
  with the same ranked search the user types into — the user's own foods and
  verified rows rank first — and the local per-100 g values win;
* only when nothing local plausibly matches does the item keep the model's
  macros, flagged ``source=ai`` (the review offers "save as my food");
* nothing is written to the diary here. ``POST /nutrition/meals`` with the
  reviewed items and ``ai_job_id`` is the only way an AI result becomes a meal.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import runner
from app.ai.prompt_store import load_prompt
from app.ai.schema import AiItem
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.logging import get_logger
from app.core.tz import utcnow
from app.models import AiJob, AiJobKind, AiJobStatus, Food, FoodFavorite, MealLog, User, UserSettings
from app.services import food_search, media

log = get_logger(__name__)

CANDIDATES = 8
PERSONAL_CANDIDATES = 20
ALTERNATIVES = 5

MEAL_HINTS = {
    "ru": {"breakfast": "завтрак", "lunch": "обед", "dinner": "ужин", "snack": "перекус", "other": "приём пищи"},
    "en": {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snack": "snack", "other": "a meal"},
}


def confidence_level(value: float) -> str:
    return "high" if value >= 0.75 else "medium" if value >= 0.45 else "low"


def _plausible(food_kcal: float, ai_kcal: float) -> bool:
    """Same food? Energy per 100 g must be in the same ballpark.

    Stops "сыр" (cheese, 350 kcal) from resolving to "сырники" or a 15 kcal
    cucumber row from absorbing an AI "pickles in oil". Tolerant on purpose:
    cooked vs raw and brand differences are normal.
    """
    hi, lo = max(food_kcal, ai_kcal), min(food_kcal, ai_kcal)
    return hi - lo <= 60 or (lo > 0 and hi / lo <= 1.6)


_TOKEN = re.compile(r"[a-zа-яё]+", re.IGNORECASE)
_STOP = {"с", "и", "в", "на", "без", "из", "по", "для", "with", "and", "in", "of", "the", "a"}


def _tokens(name: str | None) -> list[str]:
    return [t for t in _TOKEN.findall((name or "").lower().replace("ё", "е")) if len(t) >= 2 and t not in _STOP]


def _same_word(a: str, b: str) -> bool:
    """Crude Russian/English stemming: a shared prefix tolerant of endings.

    "грудка/грудки", "яйцо/яйца", "огурец/огурцы", "гречка/гречневая" match;
    "сыр/сырники" and "голень/грудка" do not.
    """
    if a == b:
        return True
    if abs(len(a) - len(b)) > 3:
        return False
    prefix = 0
    for left, right in zip(a, b, strict=False):
        if left != right:
            break
        prefix += 1
    return prefix >= max(3, min(len(a), len(b)) - 2)


def name_overlap(ai_name: str, food_name: str | None) -> float:
    """Share of the AI name's words that appear in the food's name (0..1)."""
    wanted = _tokens(ai_name)
    have = _tokens(food_name)
    if not wanted or not have:
        return 0.0
    return sum(1 for word in wanted if any(_same_word(word, other) for other in have)) / len(wanted)


def _normalised(column):
    return func.replace(func.lower(func.coalesce(column, "")), "ё", "е")


async def _personal_candidates(db: AsyncSession, user_id: uuid.UUID, item: AiItem) -> list[food_search.FoodHit]:
    """The user's own and favourite foods sharing a word stem with the AI name.

    The ranked search needs every query word, so a personal "Гречка" never
    comes back for "Гречка отварная". Personal lists are small; one query.
    """
    stems = {word[: max(3, len(word) - 2)] for word in (*_tokens(item.name_ru), *_tokens(item.name_en))}
    if not stems:
        return []
    word_match = or_(
        *(
            condition
            for stem in sorted(stems)
            for condition in (
                _normalised(Food.name_ru).contains(stem, autoescape=True),
                _normalised(Food.name_en).contains(stem, autoescape=True),
            )
        )
    )
    favorite_ids = select(FoodFavorite.food_id).where(FoodFavorite.user_id == user_id)
    rows = await db.scalars(
        select(Food)
        .where(
            Food.is_archived.is_(False),
            or_(Food.user_id == user_id, Food.id.in_(favorite_ids)),
            word_match,
        )
        .order_by(Food.use_count.desc())
        .limit(PERSONAL_CANDIDATES)
    )
    return [food_search.FoodHit(food=food, score=0.0, match_method="trgm") for food in rows]


async def _resolve(
    db: AsyncSession, user_id: uuid.UUID, item: AiItem
) -> tuple[food_search.FoodHit | None, list[food_search.FoodHit]]:
    """Pick the local food this AI item most plausibly is.

    Candidates come from the ranked search (ru name, then en name) plus the
    user's own/favourite foods sharing a word with the AI name. A candidate
    is eligible when at least half of the AI name's words occur in its name and
    its energy per 100 g is in the same ballpark. Among eligible ones the order
    is: the user's own food → every word of the AI name present → verified →
    word overlap → closest energy → search score. So a personal "Гречка" beats
    a library "Гречка отварная", and "Куриная грудка запечённая" resolves to
    "Куриная грудка приготовленная" (same words, same 165 kcal), not to
    "Куриная голень запечённая" which the search ranks higher.
    """
    collected: dict[uuid.UUID, food_search.FoodHit] = {}
    for query in dict.fromkeys((item.name_ru, item.name_en)):
        hits, _took, _more = await food_search.search_foods(db, user_id=user_id, query=query, limit=CANDIDATES)
        for hit in hits:
            collected.setdefault(hit.food.id, hit)
    for hit in await _personal_candidates(db, user_id, item):
        collected.setdefault(hit.food.id, hit)

    def ranking(hit: food_search.FoodHit) -> tuple:
        food = hit.food
        overlap = max(name_overlap(item.name_ru, food.name_ru), name_overlap(item.name_en, food.name_en))
        kcal = float(food.kcal_per_100g)
        distance = abs(kcal - item.kcal_per_100g) / max(kcal, item.kcal_per_100g, 1.0)
        return (
            food.user_id is not None,
            overlap >= 0.99,
            food.verified,
            round(overlap, 2),
            -round(distance, 2),
            hit.score,
        )

    eligible = [
        hit
        for hit in collected.values()
        if _plausible(float(hit.food.kcal_per_100g), item.kcal_per_100g)
        and max(name_overlap(item.name_ru, hit.food.name_ru), name_overlap(item.name_en, hit.food.name_en)) >= 0.5
    ]
    chosen = max(eligible, key=ranking) if eligible else None
    alternatives = sorted(
        (hit for hit in collected.values() if chosen is None or hit.food.id != chosen.food.id),
        key=ranking,
        reverse=True,
    )
    return chosen, alternatives[:ALTERNATIVES]


@dataclass(slots=True)
class ResolvedItem:
    """One AI suggestion and what the local library made of it."""

    item: AiItem
    match: food_search.FoodHit | None
    alternatives: list[food_search.FoodHit]


@dataclass(slots=True)
class AiBudget:
    """The soft daily cap is global (one server, one API key); the counts are not."""

    enabled: bool
    provider: str | None
    my_calls_today: int
    all_calls_today: int
    daily_limit: int

    @property
    def remaining(self) -> int | None:
        return max(0, self.daily_limit - self.all_calls_today) if self.daily_limit > 0 else None


@dataclass(slots=True)
class MealAnalysis:
    """Everything the review screen needs; the presenter shapes the response."""

    job: AiJob
    kind: str  # "photo" | "text"
    prompt_label: str
    took_ms: int
    items: list[ResolvedItem]
    notes: str | None
    thumbnail_kept: bool
    budget: AiBudget
    favorites: dict[uuid.UUID, float | None] = field(default_factory=dict)


async def _resolve_all(db: AsyncSession, user_id: uuid.UUID, items: list[AiItem]) -> list[ResolvedItem]:
    resolved: list[ResolvedItem] = []
    for item in items:
        chosen, alternatives = await _resolve(db, user_id, item)
        resolved.append(ResolvedItem(item=item, match=chosen, alternatives=alternatives))
    return resolved


async def budget(db: AsyncSession, *, user_id: uuid.UUID, tz: str | None) -> AiBudget:
    settings = get_settings()
    return AiBudget(
        enabled=settings.ai_enabled,
        provider=settings.ai_provider if settings.ai_enabled else None,
        my_calls_today=await runner.user_calls_today(db, user_id, tz),
        all_calls_today=await runner.calls_today(db, tz),
        daily_limit=settings.ai_daily_call_limit,
    )


def _locale(language: str | None) -> str:
    return "en" if (language or "").startswith("en") else "ru"


def _store_pending_thumbnail(job: AiJob, thumbnail: bytes) -> bool:
    """Best effort: a full disk or a read-only volume must not fail the analysis."""
    relative = media.pending_thumb_relpath(job.id)
    try:
        media.write(relative, thumbnail)
    except OSError as error:
        log.warning("ai_thumbnail_not_stored", job_id=str(job.id), error=str(error))
        return False
    job.input_thumb_path = relative
    return True


async def _finish(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    tz: str | None,
    run: runner.AiRun,
    kind: str,
    prompt_label: str,
    thumbnail_kept: bool,
) -> MealAnalysis:
    items = await _resolve_all(db, user_id, run.result.items)
    run.job.result = {**run.job.result, "matched": sum(1 for item in items if item.match), "items_out": len(items)}
    await db.flush()
    return MealAnalysis(
        job=run.job,
        kind=kind,
        prompt_label=prompt_label,
        took_ms=run.took_ms,
        items=items,
        notes=run.result.notes,
        thumbnail_kept=thumbnail_kept,
        budget=await budget(db, user_id=user_id, tz=tz),
        favorites=await food_search.favorite_ids(db, user_id),
    )


async def analyze_photo(
    db: AsyncSession,
    *,
    user: User,
    tz: str | None,
    language: str | None,
    keep_thumbnail: bool,
    photo: bytes,
    thumbnail: bytes | None,
    meal_type: str | None,
) -> MealAnalysis:
    info = media.check_photo(photo)
    # an unusable thumbnail is dropped, never an error: it is optional
    thumb_ok = keep_thumbnail and thumbnail is not None and media.check_thumbnail(thumbnail) is not None
    locale = _locale(language)
    prompt = load_prompt("analyze_meal_photo")
    run = await runner.run_meal_ai(
        db,
        user_id=user.id,
        tz=tz,
        kind=AiJobKind.ANALYZE_MEAL_PHOTO,
        prompt=prompt,
        user_text=prompt.render_user(locale=locale, meal_hint=MEAL_HINTS[locale].get(meal_type or "other", "")),
        image_jpeg=photo,
        request_meta={"width": info.width, "height": info.height, "meal_type": meal_type, "locale": locale},
    )
    # the original stays in memory only; at most the opted-in 400 px thumbnail is kept
    kept = _store_pending_thumbnail(run.job, thumbnail) if thumb_ok and thumbnail is not None else False
    return await _finish(
        db,
        user_id=user.id,
        tz=tz,
        run=run,
        kind="photo",
        prompt_label=f"{prompt.id}.{prompt.version}",
        thumbnail_kept=kept,
    )


async def parse_text(
    db: AsyncSession,
    *,
    user: User,
    tz: str | None,
    language: str | None,
    text: str,
    meal_type: str | None,
) -> MealAnalysis:
    settings = get_settings()
    cleaned = " ".join(text.split())
    if len(cleaned) > settings.ai_max_text_chars:
        raise Unprocessable(
            "The description is too long", code="text_too_long", details={"max_chars": settings.ai_max_text_chars}
        )
    locale = _locale(language)
    prompt = load_prompt("parse_food_text")
    run = await runner.run_meal_ai(
        db,
        user_id=user.id,
        tz=tz,
        kind=AiJobKind.PARSE_FOOD_TEXT,
        prompt=prompt,
        user_text=prompt.render_user(
            locale=locale, meal_hint=MEAL_HINTS[locale].get(meal_type or "other", ""), text=cleaned
        ),
        input_text=cleaned,
        request_meta={"meal_type": meal_type, "locale": locale, "chars": len(cleaned)},
    )
    return await _finish(
        db,
        user_id=user.id,
        tz=tz,
        run=run,
        kind="text",
        prompt_label=f"{prompt.id}.{prompt.version}",
        thumbnail_kept=False,
    )


async def attach_job_to_meal(db: AsyncSession, *, user: User, meal: MealLog, job_id: uuid.UUID) -> None:
    """Link a saved meal to its analysis and settle the photo thumbnail.

    Called from ``create_meal_log`` after the user confirmed the reviewed items.
    """
    # row lock: two concurrent saves of one analysis serialise here
    job = await db.scalar(select(AiJob).where(AiJob.id == job_id, AiJob.user_id == user.id).with_for_update())
    if job is None or job.kind not in (AiJobKind.ANALYZE_MEAL_PHOTO, AiJobKind.PARSE_FOOD_TEXT):
        raise NotFound("AI analysis not found", code="ai_job_not_found")
    if job.status != AiJobStatus.SUCCEEDED:
        raise Unprocessable("This AI analysis did not succeed", code="ai_job_not_usable")
    saved_to = (job.result or {}).get("saved_meal_id")
    if saved_to and saved_to != str(meal.id):
        # one analysis → one meal: a second save (double tap, retried request
        # with a fresh id) must not duplicate the diary entry
        raise Conflict("This AI analysis is already saved as a meal", code="ai_job_already_saved")
    meal.ai_job_id = job.id
    meal.ai_input_text = job.input_text
    if job.input_thumb_path:
        keep = bool(await db.scalar(select(UserSettings.keep_photo_thumbnail).where(UserSettings.user_id == user.id)))
        if keep:
            target = media.meal_thumb_relpath(user.id, meal.id)
            if media.move(job.input_thumb_path, target):
                meal.photo_thumb_path = target
        else:
            media.delete(job.input_thumb_path)
        job.input_thumb_path = None
    job.result = {**(job.result or {}), "saved_meal_id": str(meal.id), "saved_at": utcnow().isoformat()}
    await db.flush()
    log.info("ai_meal_saved", job_id=str(job.id), meal_id=str(meal.id), photo_kept=bool(meal.photo_thumb_path))


async def ai_housekeeping() -> dict[str, int]:
    """Nightly: drop unsaved thumbnails and old audit payloads (counters stay)."""
    from datetime import timedelta

    from sqlalchemy import update

    from app.db import session_scope

    settings = get_settings()
    removed = media.prune_pending(settings.ai_pending_thumb_ttl_hours * 3600)
    cutoff = utcnow() - timedelta(days=settings.ai_job_payload_retention_days)
    async with session_scope() as db:
        result = await db.execute(
            update(AiJob)
            .where(AiJob.created_at < cutoff, AiJob.response_payload != {})
            .values(request_payload={}, response_payload={}, input_thumb_path=None)
        )
        pruned = result.rowcount or 0
        stale = await db.execute(
            update(AiJob)
            .where(
                AiJob.input_thumb_path.is_not(None),
                AiJob.created_at < utcnow() - timedelta(hours=settings.ai_pending_thumb_ttl_hours),
            )
            .values(input_thumb_path=None)
        )
    log.info("ai_housekeeping", pending_thumbs_removed=removed, payloads_pruned=pruned, stale_refs=stale.rowcount)
    return {"pending_thumbs_removed": removed, "payloads_pruned": pruned}
