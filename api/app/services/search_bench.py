"""Food-search latency benchmark (stage 2b, item 5).

The spec budget is ``took_ms < 150`` for every query with a realistically large
library. :func:`insert_synthetic_foods` grows the library with
importer-shaped rows (RU+EN names, brands, aliases; ``source=off``,
``source_id='bench-…'``) in one ``INSERT … SELECT generate_series`` so tens of
thousands of rows take seconds and run through the real search triggers.
:func:`run_benchmark` drives the production :func:`food_search.search_foods`
and reports percentiles of its own ``took_ms``.

Used by ``tests/test_stage2b.py`` and ``python -m app.cli bench-search``.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import food_search

BENCH_PREFIX = "bench-"

# Russian + English queries a person actually types: exact names, prefixes,
# typos, morphology, multi-word, brand-ish and nothing-matches.
DEFAULT_QUERIES = (
    "гречка",
    "грече",
    "куриная грудка",
    "курин",
    "творог 5",
    "творг",
    "молоко",
    "сыр",
    "борщ",
    "пельмени",
    "яблоко",
    "огурцы",
    "chicken breast",
    "chicken brest",
    "oat",
    "yogurt greek",
    "банан",
    "кефир 1%",
    "zzzzqqq",
    "шоколад горький",
)

_SYNTHETIC_SQL = text(
    """
    INSERT INTO foods (
        id, is_global, source, source_id, name_ru, name_en, aliases, brand,
        kcal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g, fiber_per_100g,
        is_liquid, verified, use_count, is_archived
    )
    SELECT
        gen_random_uuid(), true, 'off', :prefix || g,
        ru[1 + g % array_length(ru, 1)] || ' ' || ru_q[1 + (g / 7) % array_length(ru_q, 1)]
            || ' ' || br[1 + (g / 3) % array_length(br, 1)],
        en[1 + g % array_length(en, 1)] || ' ' || en_q[1 + (g / 7) % array_length(en_q, 1)]
            || ' ' || br[1 + (g / 3) % array_length(br, 1)],
        jsonb_build_array(ru[1 + (g / 5) % array_length(ru, 1)]),
        br[1 + (g / 3) % array_length(br, 1)],
        50 + g % 450, (g % 30)::numeric, (g % 25)::numeric, (g % 40)::numeric, (g % 6)::numeric,
        false, false, g % 50, false
    FROM generate_series(1, :n) AS g,
    LATERAL (SELECT
        ARRAY['Гречка','Рис','Овсянка','Курица','Говядина','Свинина','Творог','Молоко','Кефир','Сыр',
              'Йогурт','Хлеб','Печенье','Шоколад','Колбаса','Сосиски','Пельмени','Вареники','Борщ','Суп',
              'Макароны','Картофель','Яблоко','Банан','Сок','Чай','Кофе','Масло','Майонез','Кетчуп',
              'Огурцы','Помидоры','Капуста','Морковь','Лук','Рыба','Креветки','Конфеты','Мороженое','Торт'] AS ru,
        ARRAY['классический','домашний','отборный','фермерский','лёгкий','с ванилью','копчёный','жареный',
              'отварной','без сахара','ржаной','цельнозерновой','2,5%','3,2%','5%','9%','острый','сливочный'] AS ru_q,
        ARRAY['Buckwheat','Rice','Oatmeal','Chicken','Beef','Pork','Cottage cheese','Milk','Kefir','Cheese',
              'Yogurt','Bread','Cookies','Chocolate','Sausage','Frankfurters','Dumplings','Vareniki','Borscht','Soup',
              'Pasta','Potatoes','Apple','Banana','Juice','Tea','Coffee','Butter','Mayonnaise','Ketchup',
              'Cucumbers','Tomatoes','Cabbage','Carrots','Onion','Fish','Shrimp','Candies','Ice cream','Cake'] AS en,
        ARRAY['classic','homemade','select','farm','light','vanilla','smoked','fried',
              'boiled','sugar free','rye','whole grain','2.5%','3.2%','5%','9%','spicy','creamy'] AS en_q,
        ARRAY['Простоквашино','Домик в деревне','Valio','Danone','Черкизово','Мираторг','Макфа','Агуша',
              'Брест-Литовск','Савушкин','Вкусвилл','Красная цена','Ашан','Лента','Магнит','Fine Life'] AS br
    ) AS vocab
    ON CONFLICT DO NOTHING
    """
)


@dataclass(slots=True)
class BenchResult:
    rows: int
    samples: int
    p50_ms: float
    p95_ms: float
    max_ms: int
    per_query_max: dict[str, int]


async def insert_synthetic_foods(db: AsyncSession, n: int) -> int:
    """Insert and commit ``n`` rows, then VACUUM ANALYZE like a finished import."""
    await db.execute(_SYNTHETIC_SQL, {"n": n, "prefix": BENCH_PREFIX})
    await db.commit()
    await vacuum_analyze_foods()
    return n


async def vacuum_analyze_foods() -> None:
    """Fresh statistics + visibility map after a bulk load.

    Without it the first searches after a big import read every heap page to
    check row visibility (and write hint bits) — exactly the latency spike the
    150 ms budget forbids. Autovacuum would get there eventually; importers and
    the benchmark do not wait for it. VACUUM cannot run inside a transaction,
    hence the dedicated autocommit connection.
    """
    from app.db import engine

    async with engine.connect() as conn:
        autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text("VACUUM (ANALYZE) foods"))


async def remove_synthetic_foods(db: AsyncSession) -> int:
    result = await db.execute(
        text("DELETE FROM foods WHERE source = 'off' AND source_id LIKE :pattern"), {"pattern": f"{BENCH_PREFIX}%"}
    )
    return result.rowcount or 0


async def run_benchmark(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    rounds: int = 3,
    limit: int = 24,
) -> BenchResult:
    total = await db.scalar(text("SELECT count(*) FROM foods"))
    timings: list[int] = []
    per_query: dict[str, int] = {}
    # one warm-up pass: the first query of a fresh connection pays for plans/caches
    for query in queries:
        await food_search.search_foods(db, user_id=user_id, query=query, limit=limit)
    for _ in range(rounds):
        for query in queries:
            _, took_ms, _ = await food_search.search_foods(db, user_id=user_id, query=query, limit=limit)
            timings.append(took_ms)
            per_query[query] = max(per_query.get(query, 0), took_ms)
    ordered = sorted(timings)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    return BenchResult(
        rows=int(total or 0),
        samples=len(timings),
        p50_ms=float(statistics.median(ordered)),
        p95_ms=float(p95),
        max_ms=ordered[-1],
        per_query_max=per_query,
    )
