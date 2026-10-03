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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import runner
from app.ai.prompt_store import load_prompt
from app.ai.schema import AiItem, AiMealResult
from app.api.v1.presenters import food_out
from app.core.config import get_settings
from app.core.errors import NotFound, Unprocessable
from app.core.logging import get_logger
from app.core.tz import utcnow
from app.models import AiJob, AiJobKind, AiJobStatus, MealLog, User, UserSettings
from app.schemas.ai import AiAnalysisOut, AiItemOut, AiMatchOut, AiPer100Out, AiUsageOut
from app.services import food_search, media

log = get_logger(__name__)

CANDIDATES = 8
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


async def _resolve(
    db: AsyncSession, user_id: uuid.UUID, item: AiItem
) -> tuple[food_search.FoodHit | None, list[food_search.FoodHit]]:
    """Pick the local food this AI item most plausibly is.

    Candidates come from the ranked search (ru name, then en name). A candidate
    is eligible when at least half of the AI name's words occur in its name and
    its energy per 100 g is in the same ballpark. Among eligible ones the order
    is: word overlap → the user's own food → verified → closest energy → search
    score. "Куриная грудка запечённая" thus resolves to "Куриная грудка
    приготовленная" (same words, same 165 kcal), not to "Куриная голень
    запечённая" which the search ranks higher.
    """
    collected: dict[uuid.UUID, food_search.FoodHit] = {}
    for query in dict.fromkeys((item.name_ru, item.name_en)):
        hits, _took, _more = await food_search.search_foods(db, user_id=user_id, query=query, limit=CANDIDATES)
        for hit in hits:
            collected.setdefault(hit.food.id, hit)

    def ranking(hit: food_search.FoodHit) -> tuple:
        food = hit.food
        overlap = max(name_overlap(item.name_ru, food.name_ru), name_overlap(item.name_en, food.name_en))
        kcal = float(food.kcal_per_100g)
        distance = abs(kcal - item.kcal_per_100g) / max(kcal, item.kcal_per_100g, 1.0)
        return (round(overlap, 2), food.user_id is not None, food.verified, -round(distance, 2), hit.score)

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


async def _items_out(db: AsyncSession, user_id: uuid.UUID, result: AiMealResult) -> list[AiItemOut]:
    favorites = await food_search.favorite_ids(db, user_id)
    out: list[AiItemOut] = []
    for index, item in enumerate(result.items):
        chosen, alternatives = await _resolve(db, user_id, item)
        factor = item.estimated_grams / 100
        out.append(
            AiItemOut(
                key=f"i{index}",
                name_ru=item.name_ru,
                name_en=item.name_en,
                estimated_grams=round(item.estimated_grams, 1),
                confidence=round(item.confidence, 2),
                confidence_level=confidence_level(item.confidence),
                kcal=round(item.kcal_per_100g * factor, 1),
                protein=round(item.protein_per_100g * factor, 1),
                fat=round(item.fat_per_100g * factor, 1),
                carbs=round(item.carbs_per_100g * factor, 1),
                fiber=round(item.fiber_per_100g * factor, 1),
                ai_per_100g=AiPer100Out(
                    kcal_per_100g=item.kcal_per_100g,
                    protein_per_100g=item.protein_per_100g,
                    fat_per_100g=item.fat_per_100g,
                    carbs_per_100g=item.carbs_per_100g,
                    fiber_per_100g=item.fiber_per_100g,
                ),
                kcal_adjusted=item.kcal_adjusted,
                match=(
                    AiMatchOut(
                        food=food_out(chosen.food, favorites=favorites),
                        match_method=chosen.match_method,
                        score=round(chosen.score, 3),
                    )
                    if chosen
                    else None
                ),
                alternatives=[food_out(hit.food, favorites=favorites) for hit in alternatives],
            )
        )
    return out


async def usage(db: AsyncSession, tz: str | None) -> AiUsageOut:
    settings = get_settings()
    used = await runner.calls_today(db, tz)
    limit = settings.ai_daily_call_limit
    return AiUsageOut(
        enabled=settings.ai_enabled,
        provider=settings.ai_provider if settings.ai_enabled else None,
        calls_today=used,
        daily_limit=limit,
        remaining=max(0, limit - used) if limit > 0 else None,
    )


def _locale(language: str | None) -> str:
    return "en" if (language or "").startswith("en") else "ru"


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
) -> AiAnalysisOut:
    info = media.check_photo(photo)
    thumb_info = media.check_thumbnail(thumbnail) if (keep_thumbnail and thumbnail) else None
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
    kept = False
    if thumb_info is not None and thumbnail is not None:
        relative = media.pending_thumb_relpath(run.job.id)
        media.write(relative, thumbnail)
        run.job.input_thumb_path = relative
        kept = True
    items = await _items_out(db, user.id, run.result)
    run.job.result = {**run.job.result, "matched": sum(1 for item in items if item.match), "items_out": len(items)}
    await db.flush()
    return AiAnalysisOut(
        job_id=run.job.id,
        kind="photo",
        provider=run.job.provider or "",
        model=run.job.model or "",
        prompt_version=f"{prompt.id}.{prompt.version}",
        took_ms=run.took_ms,
        items=items,
        notes=run.result.notes,
        thumbnail_kept=kept,
        usage=await usage(db, tz),
    )


async def parse_text(
    db: AsyncSession,
    *,
    user: User,
    tz: str | None,
    language: str | None,
    text: str,
    meal_type: str | None,
) -> AiAnalysisOut:
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
    items = await _items_out(db, user.id, run.result)
    run.job.result = {**run.job.result, "matched": sum(1 for item in items if item.match), "items_out": len(items)}
    await db.flush()
    return AiAnalysisOut(
        job_id=run.job.id,
        kind="text",
        provider=run.job.provider or "",
        model=run.job.model or "",
        prompt_version=f"{prompt.id}.{prompt.version}",
        took_ms=run.took_ms,
        items=items,
        notes=run.result.notes,
        thumbnail_kept=False,
        usage=await usage(db, tz),
    )


async def attach_job_to_meal(db: AsyncSession, *, user: User, meal: MealLog, job_id: uuid.UUID) -> None:
    """Link a saved meal to its analysis and settle the photo thumbnail.

    Called from ``create_meal_log`` after the user confirmed the reviewed items.
    """
    job = await db.scalar(select(AiJob).where(AiJob.id == job_id, AiJob.user_id == user.id))
    if job is None or job.kind not in (AiJobKind.ANALYZE_MEAL_PHOTO, AiJobKind.PARSE_FOOD_TEXT):
        raise NotFound("AI analysis not found", code="ai_job_not_found")
    if job.status != AiJobStatus.SUCCEEDED:
        raise Unprocessable("This AI analysis did not succeed", code="ai_job_not_usable")
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
