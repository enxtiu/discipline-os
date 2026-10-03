"""One AI call, done properly: budget → audit row → provider → validate → one retry.

Order of operations (each step exists because of a failure mode):

1. **Enabled?** No key and not ``fake`` → 503 ``ai_disabled``.
2. **Soft daily budget** (``AI_DAILY_CALL_LIMIT``, all users) → 429 ``ai_daily_limit``.
3. **Audit row** (``ai_jobs``, status ``running``) and usage counter, then the
   transaction is **committed before the network call** so no pooled
   connection idles inside a transaction for up to ``AI_TIMEOUT_SECONDS``.
4. **Provider call** with the versioned prompt and the strict schema.
5. **Validation** (JSON parse + :class:`AiMealResult` bounds). On an invalid
   reply the request is repeated **once** with the validation error appended;
   a transient provider failure (timeout/429/5xx) is also retried once.
   Permanent failures (bad key, bad request) are not retried.
6. The job row gets status, tokens, latency, the raw reply and the validated
   result. Nothing here writes a meal.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompt_store import Prompt
from app.ai.providers import AiProvider, AiProviderError, AiRequest, AiResponse, get_provider
from app.ai.schema import MEAL_ITEMS_SCHEMA, SCHEMA_NAME, AiMealResult
from app.core.config import get_settings
from app.core.errors import AppError, RateLimited
from app.core.logging import get_logger
from app.core.tz import today_in, utcnow
from app.models import AiJob, AiJobKind, AiJobStatus, AiUsageDaily

log = get_logger(__name__)

RETRY_DELAY_SECONDS = 0.8


class AiDisabled(AppError):
    status_code = 503
    code = "ai_disabled"


class AiUnavailable(AppError):
    """The provider failed (network, quota, outage). Safe to try again later."""

    status_code = 503
    code = "ai_unavailable"


class AiDailyLimit(RateLimited):
    code = "ai_daily_limit"


class AiInvalidResponse(AppError):
    """The model twice returned something that does not fit the schema."""

    status_code = 502
    code = "ai_invalid_response"


@dataclass(slots=True)
class AiRun:
    job: AiJob
    result: AiMealResult
    took_ms: int


def _validate(text: str) -> AiMealResult:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"not valid JSON ({error.msg} at {error.pos})") from error
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object with 'items' and 'notes'")
    try:
        return AiMealResult.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}" for issue in error.errors()[:5]
        )
        raise ValueError(problems) from error


async def calls_today(db: AsyncSession, tz: str | None) -> int:
    """All users' AI calls on the caller's local day (the soft cap is global)."""
    total = await db.scalar(
        select(func.coalesce(func.sum(AiUsageDaily.calls), 0)).where(AiUsageDaily.local_day == today_in(tz))
    )
    return int(total or 0)


async def _count_call(db: AsyncSession, *, user_id, tz: str | None, failed: bool, response: AiResponse | None) -> None:
    values = {
        "calls": 1,
        "failed_calls": 1 if failed else 0,
        "input_tokens": (response.input_tokens or 0) if response else 0,
        "output_tokens": (response.output_tokens or 0) if response else 0,
    }
    stmt = pg_insert(AiUsageDaily).values(user_id=user_id, local_day=today_in(tz), cost_usd=0, **values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ai_usage_daily_user_day",
        set_={key: getattr(AiUsageDaily, key) + stmt.excluded[key] for key in values},
    )
    await db.execute(stmt)


async def run_meal_ai(
    db: AsyncSession,
    *,
    user_id,
    tz: str | None,
    kind: AiJobKind,
    prompt: Prompt,
    user_text: str,
    image_jpeg: bytes | None = None,
    input_text: str | None = None,
    request_meta: dict | None = None,
    provider: AiProvider | None = None,
) -> AiRun:
    settings = get_settings()
    if not settings.ai_enabled:
        raise AiDisabled("AI is not configured on this server (AI_API_KEY)")
    if settings.ai_daily_call_limit > 0 and await calls_today(db, tz) >= settings.ai_daily_call_limit:
        raise AiDailyLimit(3600, "The daily AI budget is used up; try again tomorrow")
    provider = provider or get_provider(settings)
    model = settings.ai_model_vision if image_jpeg is not None else settings.ai_model_text
    if provider.name == "fake":
        model = "fake"

    job = AiJob(
        user_id=user_id,
        kind=kind,
        status=AiJobStatus.RUNNING,
        provider=provider.name,
        model=model[:64],
        prompt_id=prompt.id,
        prompt_version=prompt.version,
        input_text=input_text,
        request_payload={"meta": request_meta or {}, "image_bytes": len(image_jpeg) if image_jpeg else 0},
    )
    db.add(job)
    await db.flush()
    await db.commit()  # never hold a transaction across the provider call

    base = AiRequest(
        model=model,
        system=prompt.system,
        user_text=user_text,
        schema=MEAL_ITEMS_SCHEMA,
        schema_name=SCHEMA_NAME,
        image_jpeg=image_jpeg,
    )
    started = time.perf_counter()
    last_error = ""
    raw_replies: list[str] = []
    result: AiMealResult | None = None
    attempt = 0
    response: AiResponse | None = None
    last_kind = ""  # "invalid" (reply did not validate) or "provider" (call failed)
    for attempt in (1, 2):
        request = base
        if attempt == 2 and raw_replies:
            # repeat with the reason the previous reply was rejected
            request = AiRequest(
                model=base.model,
                system=base.system,
                user_text=(
                    f"{base.user_text}\n\nYour previous reply was rejected: {last_error}. "
                    "Reply again with JSON that matches the schema exactly."
                ),
                schema=base.schema,
                schema_name=base.schema_name,
                image_jpeg=base.image_jpeg,
            )
        try:
            response = await provider.complete(request)
        except AiProviderError as error:
            last_error = str(error)
            last_kind = "provider"
            await _count_call(db, user_id=user_id, tz=tz, failed=True, response=None)
            log.warning(
                "ai_call_failed", job_id=str(job.id), attempt=attempt, transient=error.transient, error=str(error)
            )
            if not error.transient:
                break
            if attempt == 1:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
            continue
        raw_replies.append(response.text[:20_000])
        try:
            result = _validate(response.text)
        except ValueError as error:
            last_error = str(error)[:500]
            last_kind = "invalid"
            await _count_call(db, user_id=user_id, tz=tz, failed=True, response=response)
            log.warning("ai_reply_invalid", job_id=str(job.id), attempt=attempt, error=last_error)
            continue
        await _count_call(db, user_id=user_id, tz=tz, failed=False, response=response)
        break

    took_ms = int((time.perf_counter() - started) * 1000)
    job.attempt = attempt
    job.latency_ms = took_ms
    job.finished_at = utcnow()
    job.response_payload = {"replies": raw_replies, "provider": response.raw if response else {}}
    if response is not None:
        job.input_tokens = response.input_tokens
        job.output_tokens = response.output_tokens
    if result is None:
        job.status = AiJobStatus.FAILED
        job.error = last_error[:2000]
        await db.commit()
        log.info("ai_job_failed", job_id=str(job.id), kind=kind.value, attempts=attempt, took_ms=took_ms)
        if last_kind == "invalid":
            raise AiInvalidResponse("The AI reply could not be understood; please try again")
        raise AiUnavailable("The AI service is not available right now; please try again")
    job.status = AiJobStatus.SUCCEEDED
    job.result = result.model_dump(mode="json")
    await db.flush()
    log.info(
        "ai_job_succeeded",
        job_id=str(job.id),
        kind=kind.value,
        provider=provider.name,
        model=model,
        prompt=f"{prompt.id}.{prompt.version}",
        attempts=attempt,
        items=len(result.items),
        took_ms=took_ms,
        input_tokens=job.input_tokens,
        output_tokens=job.output_tokens,
    )
    return AiRun(job=job, result=result, took_ms=took_ms)
