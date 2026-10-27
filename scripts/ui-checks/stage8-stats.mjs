/**
 * Browser checks — stage 8 stats dashboards (/stats) and Settings → Данные
 * (JSON / CSV / backup downloads). Called by scripts/ui-check.mjs after the
 * stage-8 game module.
 *
 * Seeds a few days through the API (meals, water, steps, a weigh-in, sleep, a
 * strength workout, a task, a timer entry), then checks that every section
 * shows the API's numbers, the period switcher refetches, the zone band and the
 * target line are drawn, burned calories live in Активность only, a tap opens a
 * day's readout, an empty period shows empty states, the light theme stays
 * readable, the three downloads arrive with the server's filenames and the
 * right contents, and a burst gets «Слишком часто — попробуйте через минуту».
 * Everything this module logs is removed at the end.
 */
import { createHash, randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { inflateRawSync } from 'node:zlib'

/** Unicode spaces from ru-RU number formatting → plain spaces. */
const norm = (value) => String(value ?? '').replace(/[   ​]/g, ' ').trim()
/** The app's `number()` (ru-RU, fixed digits). */
const fmt = (value, digits = 0) =>
  value == null ? '—' : norm(new Intl.NumberFormat('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value))
/** The app's `hoursMinutes()`. */
const hm = (minutes) => {
  if (minutes == null) return '—'
  const total = Math.round(minutes)
  const hours = Math.floor(total / 60)
  const rest = total % 60
  if (hours === 0) return `${rest} мин`
  return rest === 0 ? `${fmt(hours)} ч` : `${fmt(hours)} ч ${rest} мин`
}
const sum = (values) => values.reduce((total, value) => total + (value ?? 0), 0)
const mean = (values) => {
  const present = values.filter((value) => value != null)
  return present.length ? sum(present) / present.length : null
}
const addDays = (day, delta) => {
  const date = new Date(`${day}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + delta)
  return date.toISOString().slice(0, 10)
}
/** What the kcal card says about a day — from the API's zone, floor and in_zone. */
const kcalStatus = (day, stats) => {
  if (day.date === stats.today) return 'день идёт'
  if (day.in_zone === true) return 'в зоне цели'
  if (day.kcal < stats.floor) return 'ниже безопасного минимума'
  if (!stats.zone) return 'записано'
  return day.kcal < (stats.zone.low + stats.zone.high) / 2 ? 'ниже зоны' : 'выше зоны'
}
const monthLabel = (day) => {
  const label = new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`))
  return norm(label.charAt(0).toUpperCase() + label.slice(1))
}

/** Entries of a zip (central directory) with their bytes inflated on demand. */
function readZip(buffer) {
  const entries = new Map()
  for (let end = buffer.length - 22; end >= Math.max(0, buffer.length - 65_557); end -= 1) {
    if (buffer.readUInt32LE(end) !== 0x06054b50) continue
    const count = buffer.readUInt16LE(end + 10)
    let offset = buffer.readUInt32LE(end + 16)
    for (let index = 0; index < count && buffer.readUInt32LE(offset) === 0x02014b50; index += 1) {
      const method = buffer.readUInt16LE(offset + 10)
      const compressed = buffer.readUInt32LE(offset + 20)
      const nameLength = buffer.readUInt16LE(offset + 28)
      const extraLength = buffer.readUInt16LE(offset + 30)
      const commentLength = buffer.readUInt16LE(offset + 32)
      const local = buffer.readUInt32LE(offset + 42)
      const name = buffer.toString('utf8', offset + 46, offset + 46 + nameLength)
      entries.set(name, () => {
        const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28)
        const data = buffer.subarray(start, start + compressed)
        return method === 8 ? inflateRawSync(data) : Buffer.from(data)
      })
      offset += 46 + nameLength + extraLength + commentLength
    }
    break
  }
  return entries
}

export async function run(ctx) {
  const { page, WEB_URL, check, ok, bad, shot, allowErrors } = ctx
  console.log('\n7¹⁵. stage 8 stats dashboards & data export')

  const apiFetch = (path, init = {}) =>
    page.evaluate(
      async ({ path, init }) => {
        const token = window.localStorage.getItem('dsos.device_token')
        const response = await fetch(`/api${path}`, {
          ...init,
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
        })
        const raw = await response.text()
        let body = null
        try {
          body = raw ? JSON.parse(raw) : null
        } catch {
          body = raw
        }
        return { status: response.status, body }
      },
      { path, init },
    )
  const stats = async (section, query) => (await apiFetch(`/v1/stats/${section}?${query}`)).body
  const tile = async (id) => norm(await page.locator(`[data-testid="${id}-value"]`).first().innerText())
  /** The dashboard has answered: every section rendered, nothing fetching. */
  const settled = async () => {
    await page.locator('[data-testid="stats-page"]').waitFor({ timeout: 15_000 })
    await page.waitForFunction(
      () =>
        !document.querySelector('[data-testid="stats-page"][data-loading]') &&
        !document.querySelector('[data-testid^="stats-skeleton-"]') &&
        document.querySelectorAll('[data-testid^="stats-section-"]').length === 5,
      null,
      { timeout: 20_000 },
    )
    await page.waitForTimeout(450) // bars grow in
  }
  const openStats = async (query = '') => {
    await page.goto(`${WEB_URL}/stats${query}`, { waitUntil: 'networkidle' })
    await settled()
  }

  // the module's own console errors (the harness checks the whole run as well)
  const errors = []
  let expectErrors = false
  const onConsole = (message) => {
    if (expectErrors) return
    if (message.type() === 'error' && !/favicon|404 \(Not Found\)/i.test(message.text())) errors.push(message.text())
  }
  const onPageError = (error) => errors.push(String(error))
  page.on('console', onConsole)
  page.on('pageerror', onPageError)

  const created = { meals: [], water: [], steps: [], weight: null, sleep: null, workout: null, task: null, entry: null }
  let T = null

  try {
    // ------------------------------------------------------------------ //
    // 1. a few days of data through the API
    // ------------------------------------------------------------------ //
    T = (await stats('nutrition', 'period=week')).today
    const T1 = addDays(T, -1)
    const T2 = addDays(T, -2)
    const noon = (day, hour = 12) => `${day}T${String(hour).padStart(2, '0')}:00:00+03:00`
    const statuses = []
    const meal = async (day, kcal, name) => {
      const response = await apiFetch('/v1/nutrition/meals', {
        method: 'POST',
        body: JSON.stringify({
          meal_type: 'lunch',
          source: 'manual',
          logged_at: noon(day),
          items: [{ name, grams: 500, kcal_per_100g: kcal / 5, protein_per_100g: 30, fat_per_100g: 8, carbs_per_100g: 40, fiber_per_100g: 6.2 }],
        }),
      })
      statuses.push(response.status)
      if (response.status === 201 || response.status === 200) created.meals.push(response.body.id)
    }
    await meal(T1, 2000, 'Плов (ui-check stats)') // inside the zone
    await meal(T2, 1300, 'Суп (ui-check stats)') // below the safe floor — never highlighted
    const water = await apiFetch('/v1/nutrition/water', { method: 'POST', body: JSON.stringify({ amount_ml: 2700, logged_at: noon(T1, 15) }) })
    statuses.push(water.status)
    if (water.status === 201 || water.status === 200) created.water.push(water.body.id)
    for (const [day, steps, active] of [
      [T1, 9100, 360],
      [T2, 4200, 150],
    ]) {
      const put = await apiFetch(`/v1/health/steps/${day}`, { method: 'PUT', body: JSON.stringify({ steps, active_kcal: active }) })
      statuses.push(put.status)
      if (put.status === 200) created.steps.push(day)
    }
    const weight = await apiFetch(`/v1/health/weight/${T1}`, { method: 'PUT', body: JSON.stringify({ weight_kg: 80.6 }) })
    statuses.push(weight.status)
    if (weight.status === 200) created.weight = T1
    const sleep = await apiFetch(`/v1/health/sleep/${T1}`, { method: 'PUT', body: JSON.stringify({ bed_time: '23:30', wake_time: '07:15' }) })
    statuses.push(sleep.status)
    if (sleep.status === 200) created.sleep = T1
    const exercises = (await apiFetch(`/v1/training/exercises?q=${encodeURIComponent('жим лёжа')}&limit=5`)).body
    const bench = (exercises?.items ?? exercises ?? [])[0]?.id
    const workoutId = randomUUID()
    const started = await apiFetch('/v1/training/workouts', {
      method: 'POST',
      body: JSON.stringify({ id: workoutId, kind: 'strength', title: 'Силовая (ui-check stats)', started_at: noon(T1, 18) }),
    })
    statuses.push(started.status)
    if (started.status === 201 || started.status === 200) {
      created.workout = workoutId
      for (const index of [1, 2, 3]) {
        const set = await apiFetch(`/v1/training/workouts/${workoutId}/sets/${randomUUID()}`, {
          method: 'PUT',
          body: JSON.stringify({ exercise_id: bench, sort_order: 0, set_index: index, is_warmup: false, reps: 8, weight_kg: 62.5 }),
        })
        statuses.push(set.status)
      }
      const finished = await apiFetch(`/v1/training/workouts/${workoutId}/finish`, {
        method: 'POST',
        body: JSON.stringify({ ended_at: `${T1}T18:55:00+03:00` }),
      })
      statuses.push(finished.status)
    }
    const task = await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ title: 'Отчёт (ui-check stats)', priority: 2 }) })
    statuses.push(task.status)
    if (task.status === 201 || task.status === 200) {
      created.task = task.body.id
      statuses.push((await apiFetch(`/v1/planner/tasks/${task.body.id}/complete`, { method: 'POST', body: '{}' })).status)
    }
    const areas = (await apiFetch('/v1/planner/areas')).body
    const area = (areas?.items ?? areas ?? [])[0]
    const entry = await apiFetch('/v1/planner/time-entries', {
      method: 'POST',
      body: JSON.stringify({ area_id: area?.id ?? null, date: T1, start: '10:00', end: '11:30', description: 'ui-check stats' }),
    })
    statuses.push(entry.status)
    if (entry.status === 201 || entry.status === 200) created.entry = entry.body.id
    check(
      statuses.every((status) => status === 200 || status === 201) && created.meals.length === 2 && created.workout && created.entry,
      'seeded two days through the API: meals, water, steps, weigh-in, sleep, workout, task, timer entry',
      `${statuses.length} writes, days ${T2}…${T1}`,
    )

    // ------------------------------------------------------------------ //
    // 2. every section shows the API's numbers (the week containing T-1)
    // ------------------------------------------------------------------ //
    const query = `period=week&date=${T1}`
    await openStats(`?date=${T1}`)
    const [nutrition, activity, body, planner, progress] = await Promise.all(
      ['nutrition', 'activity', 'body', 'planner', 'progress'].map((section) => stats(section, query)),
    )
    await shot('60-stats-week')
    const label = page.locator('[data-testid="stats-period-label"]')
    check(
      (await label.getAttribute('data-from')) === nutrition.from && (await label.getAttribute('data-to')) === nutrition.to,
      'the period row shows the server’s week around the anchor',
      `${nutrition.from} … ${nutrition.to}: ${norm(await label.innerText())}`,
    )

    const elapsed = nutrition.days.filter((day) => day.date <= nutrition.today).length
    const shownNutrition = [await tile('stat-kcal-avg'), await tile('stat-zone-days'), await tile('stat-logged-days')]
    const wantNutrition = [fmt(nutrition.averages.kcal), fmt(nutrition.adherence.zone_days), fmt(nutrition.adherence.logged_days)]
    const adherence = await page
      .locator('[data-testid^="adherence-"]')
      .evaluateAll((els) => els.map((el) => `${el.dataset.testid}:${el.dataset.n}/${el.dataset.m}`))
    // fiber days are not in the API: the protein rule (a day with meals, up to today, at or above the
    // safe floor) applied to fiber — T-2 has the fiber but is below the floor, so it must not count
    const fiberDays = nutrition.days.filter(
      (day) =>
        day.date <= nutrition.today &&
        day.meals > 0 &&
        day.kcal >= nutrition.floor &&
        nutrition.target_fiber_g != null &&
        day.fiber_g >= nutrition.target_fiber_g,
    ).length
    const wantAdherence = [
      `adherence-protein:${nutrition.adherence.protein_days}/${nutrition.adherence.logged_days}`,
      `adherence-fiber:${fiberDays}/${nutrition.adherence.logged_days}`,
      `adherence-water:${nutrition.adherence.water_days}/${elapsed}`,
    ]
    check(
      JSON.stringify(shownNutrition) === JSON.stringify(wantNutrition) && JSON.stringify(adherence) === JSON.stringify(wantAdherence),
      'Питание: average kcal, zone days, logged days, protein / fiber / water days = GET /stats/nutrition',
      `${shownNutrition.join(' · ')} · ${adherence.join(' ')}`,
    )

    const finishedActivity = activity.days.filter((day) => day.date < activity.today)
    const wantActivity = [
      fmt(mean(finishedActivity.map((day) => day.steps))),
      fmt(activity.days.filter((day) => day.on_target === true).length),
      fmt(sum(activity.days.map((day) => day.workouts))),
      fmt(sum(activity.days.map((day) => day.workout_kcal))),
      activity.days.some((day) => day.active_kcal != null) ? fmt(sum(activity.days.map((day) => day.active_kcal))) : '—',
    ]
    const shownActivity = [
      await tile('stat-steps-avg'),
      await tile('stat-steps-target'),
      await tile('stat-workouts-total'),
      await tile('stat-burn-workouts'),
      await tile('stat-burn-active'),
    ]
    check(
      JSON.stringify(shownActivity) === JSON.stringify(wantActivity) && sum(activity.days.map((day) => day.workouts)) >= 1,
      'Активность: steps average, days on the step target, workouts, burned in workouts and active energy = GET /stats/activity',
      shownActivity.join(' · '),
    )
    const muscles = await page.locator('[data-testid="muscle-list"] li').count()
    check(muscles === activity.muscle_volume.length && muscles >= 1, 'volume per muscle group listed (horizontal bars)', `${muscles} group(s)`)

    const latest = [...body.weight].reverse().find((point) => point.kg != null)
    const wantBody = [fmt(latest?.kg ?? null, 1), hm(body.sleep_avg)]
    const shownBody = [await tile('stat-weight-latest'), await tile('stat-sleep-avg')]
    check(
      JSON.stringify(shownBody) === JSON.stringify(wantBody) && body.weight.some((point) => point.date === T1 && point.kg === 80.6),
      'Тело: latest weigh-in and average sleep = GET /stats/body',
      shownBody.join(' · '),
    )

    const wantPlanner = [
      fmt(sum(planner.days.map((day) => day.done))),
      fmt(sum(planner.days.map((day) => day.created))),
      hm(sum(planner.days.map((day) => day.tracked_minutes))),
    ]
    const shownPlanner = [await tile('stat-tasks-done'), await tile('stat-tasks-created'), await tile('stat-tracked')]
    const areaRows = await page.locator('[data-testid="area-list"] li').count()
    check(
      JSON.stringify(shownPlanner) === JSON.stringify(wantPlanner) && areaRows === planner.by_area.length,
      'План: tasks done and created, tracked time and the areas = GET /stats/planner',
      `${shownPlanner.join(' · ')} · ${areaRows} area(s)`,
    )

    const wantProgress = [fmt(sum(progress.xp.map((day) => day.xp))), fmt(progress.streak_calendar.filter((day) => day.overall).length)]
    const shownProgress = [await tile('stat-xp-total'), await tile('stat-streak-days')]
    const cells = await page.locator('[data-testid="streak-calendar"] [data-date]').count()
    check(
      JSON.stringify(shownProgress) === JSON.stringify(wantProgress) && cells === progress.streak_calendar.length,
      'Прогресс: XP of the period, streak days and one calendar cell per day = GET /stats/progress',
      `${shownProgress.join(' · ')} · ${cells} cells`,
    )
    check(
      (await page.locator('[data-testid="stats-to-achievements"]').getAttribute('href')) === '/stats/achievements',
      'the progress section links to /stats/achievements',
    )

    // ------------------------------------------------------------------ //
    // 3. the kcal chart: zone band, target line, «нет записей», T-1 in the zone
    // ------------------------------------------------------------------ //
    const chart = page.locator('[data-testid="chart-kcal"]')
    const unlogged = nutrition.days.filter((day) => day.date <= nutrition.today && day.meals === 0).length
    const drawn = await chart.evaluate((el) => ({
      zone: el.querySelectorAll('.stats-zone').length,
      target: el.querySelectorAll('.stats-target').length,
      ghosts: el.querySelectorAll('.stats-ghost path').length,
      label: el.getAttribute('aria-label') ?? '',
    }))
    check(
      drawn.zone === 1 && drawn.target === 1 && nutrition.zone != null,
      'kcal chart draws the zone band and the target line',
      `zone ${nutrition.zone?.low}–${nutrition.zone?.high}, target ${nutrition.target_kcal}`,
    )
    check(
      drawn.ghosts === unlogged && drawn.label.includes(`дней без записей: ${unlogged}`),
      'days without meals are «нет записей» placeholders, never 0 kcal (chart and screen-reader summary)',
      `${drawn.ghosts} placeholder(s), ${unlogged} unlogged day(s)`,
    )
    await page.locator('[data-testid="table-kcal"] summary').click()
    const t1Day = nutrition.days.find((day) => day.date === T1)
    const t2Day = nutrition.days.find((day) => day.date === T2)
    const rowText = async (day) => norm(await page.locator(`[data-testid="table-kcal"] tr[data-row="${day}"]`).innerText().catch(() => ''))
    const t1Row = await rowText(T1)
    const t2Row = t2Day ? await rowText(T2) : null
    check(
      t1Row.includes(fmt(t1Day?.kcal)) &&
        t1Row.includes(kcalStatus(t1Day, nutrition)) &&
        (t2Row == null || (t2Row.includes(fmt(t2Day.kcal)) && t2Row.includes(kcalStatus(t2Day, nutrition)))),
      'table view: each day’s kcal and how it sits against the zone and the safe floor = the API',
      `${t1Row} | ${t2Row ?? 'T-2 in another week'}`,
    )
    await page.locator('[data-testid="table-kcal"] summary').click()

    // a day below the safe floor is never painted like a day in the zone
    const fills = await chart.locator('.stats-bar-kcal .recharts-bar-rectangle').evaluateAll((groups) =>
      groups.map((group) => group.querySelector('path')?.getAttribute('fill') ?? null),
    )
    const zoneFill = nutrition.days.map((day, i) => (day.in_zone === true ? fills[i] : null)).find(Boolean)
    const floorDays = nutrition.days.map((day, i) => [day, fills[i]]).filter(([day]) => day.date < nutrition.today && day.meals > 0 && day.kcal < nutrition.floor)
    check(
      floorDays.every(([, fill]) => fill && fill !== zoneFill) &&
        nutrition.days.every((day, i) => day.in_zone !== true || fills[i] === zoneFill),
      'only the server’s in-zone days get the accent; days below the safe floor are not highlighted',
      `${nutrition.days.filter((day) => day.in_zone === true).length} in zone, ${floorDays.length} below the floor`,
    )

    // a tap on T-1's bar opens its readout
    const index = nutrition.days.findIndex((day) => day.date === T1)
    const bar = chart.locator('.stats-bar-kcal .recharts-bar-rectangle').nth(index).locator('path')
    await bar.scrollIntoViewIfNeeded()
    const box = await bar.boundingBox()
    if (box) await page.touchscreen.tap(box.x + box.width / 2, box.y + Math.min(box.height / 2, 20))
    const tip = chart.locator('[data-testid="chart-tooltip"]')
    const tipText = norm(await tip.innerText({ timeout: 4000 }).catch(() => ''))
    check(
      tipText.includes(`${fmt(t1Day?.kcal)} ккал`) && tipText.includes(kcalStatus(t1Day, nutrition)),
      'a tap on a day opens its readout (tooltip by tap)',
      tipText.replace(/\s+/g, ' ').slice(0, 90),
    )
    await shot('61-stats-tap')
    await page.locator('[data-testid="stats-page"] h1').click() // a tap outside closes it
    check((await tip.count()) === 0, 'a tap outside the chart closes the readout')

    // ------------------------------------------------------------------ //
    // 4. burned calories live in Активность only
    // ------------------------------------------------------------------ //
    const nutritionSection = page.locator('[data-testid="stats-section-nutrition"]')
    const nutritionText = norm(await nutritionSection.innerText()).toLowerCase()
    check(
      (await page.locator('[data-testid="stats-section-activity"] [data-testid="stats-burned"]').count()) === 1 &&
        (await nutritionSection.locator('[data-testid="stats-burned"]').count()) === 0 &&
        !nutritionText.includes('сожж') &&
        !nutritionText.includes('активная энергия') &&
        (await page.locator('[data-testid="stats-burned"]').innerText()).includes('никогда не прибавляются к цели по еде'),
      'burned calories are a card of Активность only (never in Питание), with the never-food-budget note',
    )
    const eaten = (await apiFetch(`/v1/nutrition/day?date=${T1}`)).body?.totals?.kcal
    const burned = activity.days.find((day) => day.date === T1)?.workout_kcal ?? 0
    check(
      eaten != null && Math.abs((t1Day?.kcal ?? -1) - eaten) < 0.5 && burned > 0,
      'Питание counts eaten kcal only: T-1 = the day’s meals, its workout calories are not subtracted',
      `T-1: ${fmt(t1Day?.kcal)} kcal eaten (the food diary says ${fmt(eaten)}), ${fmt(burned)} kcal burned — in Активность`,
    )

    // ------------------------------------------------------------------ //
    // 5. the period switcher changes the range and refetches
    // ------------------------------------------------------------------ //
    const [monthResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/v1/stats/nutrition') && r.url().includes('period=month'), { timeout: 10_000 }),
      page.locator('[data-testid="stats-period"]').getByRole('tab', { name: 'Месяц', exact: true }).click(),
    ])
    const month = await monthResponse.json()
    await page.waitForFunction((from) => document.querySelector('[data-testid="stats-period-label"]')?.getAttribute('data-from') === from, month.from, {
      timeout: 10_000,
    })
    await settled()
    check(
      page.url().includes('period=month') &&
        norm(await label.innerText()) === monthLabel(month.from) &&
        month.days.length >= 28 &&
        (await tile('stat-zone-days')) === fmt(month.adherence.zone_days),
      'Месяц → the month around the anchor is fetched and shown',
      `${norm(await label.innerText())} (${month.from} … ${month.to}), ${month.days.length} days`,
    )
    const monthUnlogged = month.days.filter((day) => day.date <= month.today && day.meals === 0).length
    const monthGhosts = await page.locator('[data-testid="chart-kcal"] .stats-ghost path').count()
    check(
      monthGhosts === monthUnlogged,
      'the month: every past day without meals is a «нет записей» placeholder',
      `${monthGhosts} placeholder(s) for ${monthUnlogged} unlogged day(s)`,
    )
    await shot('62-stats-month')
    const previousFrom = addDays(month.from, -1)
    const [previousResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/v1/stats/activity') && r.url().includes(`date=${previousFrom}`), { timeout: 10_000 }),
      page.locator('[data-testid="stats-prev"]').click(),
    ])
    const previous = await previousResponse.json()
    await page.waitForFunction((from) => document.querySelector('[data-testid="stats-period-label"]')?.getAttribute('data-from') === from, previous.from, {
      timeout: 10_000,
    })
    check(
      previous.period === 'month' && previous.to === addDays(month.from, -1) && norm(await label.innerText()) === monthLabel(previous.from),
      '‹ asks for the day before the period and shows the previous month',
      norm(await label.innerText()),
    )
    await settled()
    const [currentResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/v1/stats/body') && r.url().includes('period=month') && !r.url().includes('date='), {
        timeout: 10_000,
      }),
      page.locator('[data-testid="stats-current"]').click(),
    ])
    const current = await currentResponse.json()
    await page.waitForFunction((from) => document.querySelector('[data-testid="stats-period-label"]')?.getAttribute('data-from') === from, current.from, {
      timeout: 10_000,
    })
    check(
      current.from <= current.today && current.today <= current.to && (await page.locator('[data-testid="stats-next"]').isDisabled()),
      '«к текущему месяцу» returns to the server’s today; › is off at the current period',
      `${current.from} … ${current.to}`,
    )
    await settled()

    // ------------------------------------------------------------------ //
    // 6. an empty period shows the empty states
    // ------------------------------------------------------------------ //
    await openStats('?period=year&date=2001-06-01')
    const empties = await page.locator('[data-testid="stats-empty"]').count()
    check(
      empties === 4 && norm(await label.innerText()) === '2001',
      'an empty period (2001) shows empty states instead of empty charts',
      `${empties} empty state(s)`,
    )
    await shot('63-stats-empty')

    // ------------------------------------------------------------------ //
    // 7. light theme stays readable (and the charts repaint)
    // ------------------------------------------------------------------ //
    await openStats(`?date=${T1}`)
    const paint = () =>
      page.evaluate(() => {
        const channels = (value) => (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
        const luminance = (value) => {
          const [r, g, b] = channels(value).map((v) => {
            const c = v / 255
            return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
          })
          return 0.2126 * r + 0.7152 * g + 0.0722 * b
        }
        const ratio = (a, b) => {
          const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
          return Math.round(((x + 0.05) / (y + 0.05)) * 100) / 100
        }
        const card = document.querySelector('[data-testid="stats-nutrition-card"]')
        const bg = getComputedStyle(card).backgroundColor
        // the bar of today (in progress) is a translucent fill with a full-colour outline: its outline counts
        const bars = [...document.querySelectorAll('[data-testid="chart-kcal"] .stats-bar-kcal path')].map((el) => {
          const fill = el.getAttribute('fill') ?? ''
          return fill.startsWith('rgba') ? (el.getAttribute('stroke') ?? fill) : fill
        })
        const tick = document.querySelector('[data-testid="chart-kcal"] .recharts-yAxis text')?.getAttribute('fill') ?? bg
        return {
          theme: document.documentElement.dataset.theme ?? 'dark',
          bg,
          bars,
          title: ratio(getComputedStyle(card.querySelector('h3')).color, bg),
          hint: ratio(getComputedStyle(card.querySelector('h3 + p')).color, bg),
          tick: ratio(tick, bg),
          bar: Math.min(...bars.map((fill) => ratio(fill, bg))),
        }
      })
    const dark = await paint()
    await page.evaluate(() => {
      window.localStorage.setItem('dsos.theme', 'light')
      document.documentElement.dataset.theme = 'light'
      document.documentElement.classList.remove('dark')
    })
    await page.waitForTimeout(500)
    const light = await paint()
    check(
      light.theme === 'light' &&
        light.bg !== dark.bg &&
        JSON.stringify(light.bars) !== JSON.stringify(dark.bars) &&
        light.title >= 7 &&
        light.hint >= 4.5 &&
        light.tick >= 4.5 &&
        light.bar >= 3,
      'light theme: charts repaint from the tokens; text ≥ 4.5:1 and bars ≥ 3:1 on the card',
      `title ${light.title}:1, hint ${light.hint}:1, ticks ${light.tick}:1, bars ≥ ${light.bar}:1 (dark: bars ≥ ${dark.bar}:1)`,
    )
    await shot('64-stats-light')
    await page.locator('[data-testid="stats-section-activity"]').scrollIntoViewIfNeeded()
    await shot('65-stats-light-activity')
    await page.evaluate(() => {
      window.localStorage.setItem('dsos.theme', 'dark')
      document.documentElement.dataset.theme = 'dark'
      document.documentElement.classList.add('dark')
    })

    // ------------------------------------------------------------------ //
    // 8. Settings → Данные: three downloads, the server's names, the right contents
    // ------------------------------------------------------------------ //
    await page.goto(`${WEB_URL}/settings/data`, { waitUntil: 'networkidle' })
    const today = (await stats('nutrition', 'period=week')).today
    const download = async (kind) => {
      const row = page.locator(`[data-testid="export-${kind}"]`)
      await row.scrollIntoViewIfNeeded()
      const [file] = await Promise.all([
        page.waitForEvent('download', { timeout: 30_000 }),
        row.locator('[data-testid="export-download"]').click(),
      ])
      const buffer = readFileSync(await file.path())
      await row.locator('[data-testid="export-done"]').waitFor({ timeout: 10_000 })
      return { name: file.suggestedFilename(), buffer, shown: norm(await row.locator('[data-testid="export-done"]').innerText()) }
    }
    const json = await download('json')
    let exported = null
    try {
      exported = JSON.parse(json.buffer.toString('utf8'))
    } catch {
      exported = null
    }
    const exportedMeals = (exported?.tables?.meal_logs ?? []).map((row) => row.id)
    check(
      json.name === `disciplineos-${today}.json` &&
        created.meals.every((id) => exportedMeals.includes(id)) &&
        json.shown.includes(json.name),
      'JSON download: disciplineos-<today>.json with the seeded meals inside',
      `${json.name}, ${json.buffer.length} bytes, ${exportedMeals.length} meal(s) — «${json.shown}»`,
    )
    const csv = await download('csv')
    const csvZip = readZip(csv.buffer)
    const mealsCsv = csvZip.get('meal_logs.csv')?.().toString('utf8') ?? ''
    check(
      new RegExp(`^disciplineos-${today}(-csv)?\\.zip$`).test(csv.name) &&
        csvZip.has('README.txt') &&
        mealsCsv.charCodeAt(0) === 0xfeff &&
        created.meals.every((id) => mealsCsv.includes(id)),
      'CSV zip download: the server’s name, README.txt and meal_logs.csv (UTF-8 BOM) with the seeded meals',
      `${csv.name}, ${csvZip.size} files`,
    )
    const backup = await download('backup')
    const backupZip = readZip(backup.buffer)
    let manifest = null
    try {
      manifest = JSON.parse(backupZip.get('manifest.json')?.().toString('utf8') ?? 'null')
    } catch {
      manifest = null
    }
    const data = backupZip.get('data.json')?.()
    const digest = data ? createHash('sha256').update(data).digest('hex') : null
    const expected = manifest?.files?.['data.json']?.sha256
    check(
      new RegExp(`^disciplineos-${today}(-backup)?\\.zip$`).test(backup.name) &&
        data != null &&
        (manifest?.counts?.meal_logs ?? 0) >= 2 &&
        (expected == null || expected === digest),
      'backup zip download: the server’s name, manifest.json (counts, checksum) and an intact data.json',
      `${backup.name}, meal_logs ${manifest?.counts?.meal_logs}, sha256 ${expected ? (expected === digest ? 'matches' : 'MISMATCH') : 'n/a'}`,
    )
    check(
      (await page.locator('[data-testid="restore-command"]').innerText()).includes('restore-backup') &&
        (await page.locator('[data-testid="data-restore"]').innerText()).includes('Backups and restore'),
      'how to restore: the CLI command and the README section',
    )
    await page.locator('[data-testid="export-json"]').scrollIntoViewIfNeeded()
    await shot('66-settings-data')

    // ------------------------------------------------------------------ //
    // 9. a burst gets the 429 message
    // ------------------------------------------------------------------ //
    expectErrors = true
    const burst = await allowErrors(async () => {
      const seen = []
      for (let attempt = 0; attempt < 8 && !seen.includes(429); attempt += 1) {
        seen.push(
          await page.evaluate(async () => {
            const token = window.localStorage.getItem('dsos.device_token')
            const response = await fetch('/api/v1/export/json', { headers: { Authorization: `Bearer ${token}` } })
            await response.arrayBuffer()
            return response.status
          }),
        )
      }
      await page.locator('[data-testid="export-json"] [data-testid="export-download"]').click()
      await page.locator('[data-testid="export-json"] [data-testid="export-error"]').waitFor({ timeout: 10_000 })
      await page.waitForTimeout(300)
      return seen
    })
    expectErrors = false
    const message = norm(await page.locator('[data-testid="export-json"] [data-testid="export-error"]').innerText())
    check(
      burst.includes(429) && message === 'Слишком часто — попробуйте через минуту',
      'a burst of exports answers 429 → «Слишком часто — попробуйте через минуту»',
      `statuses ${burst.join(', ')} → ${message}`,
    )
    await shot('67-settings-data-429')
  } catch (error) {
    expectErrors = false
    bad('stage 8 stats checks', String(error?.message ?? error))
  } finally {
    page.off('console', onConsole)
    page.off('pageerror', onPageError)
    await page
      .evaluate(() => {
        window.localStorage.setItem('dsos.theme', 'dark')
        document.documentElement.dataset.theme = 'dark'
        document.documentElement.classList.add('dark')
      })
      .catch(() => {})
    check(errors.length === 0, 'no console errors during the stage-8 stats checks', errors.slice(0, 2).join(' | '))
    // clean up everything this module logged
    const statuses = []
    for (const id of created.meals) statuses.push((await apiFetch(`/v1/nutrition/meals/${id}`, { method: 'DELETE' })).status)
    for (const id of created.water) statuses.push((await apiFetch(`/v1/nutrition/water/${id}`, { method: 'DELETE' })).status)
    for (const day of created.steps) statuses.push((await apiFetch(`/v1/health/steps/${day}`, { method: 'DELETE' })).status)
    if (created.weight) statuses.push((await apiFetch(`/v1/health/weight/${created.weight}`, { method: 'DELETE' })).status)
    if (created.sleep) statuses.push((await apiFetch(`/v1/health/sleep/${created.sleep}`, { method: 'DELETE' })).status)
    if (created.workout) statuses.push((await apiFetch(`/v1/training/workouts/${created.workout}`, { method: 'DELETE' })).status)
    if (created.task) statuses.push((await apiFetch(`/v1/planner/tasks/${created.task}`, { method: 'DELETE' })).status)
    if (created.entry) statuses.push((await apiFetch(`/v1/planner/time-entries/${created.entry}`, { method: 'DELETE' })).status)
    const remaining = []
    for (const day of T ? [addDays(T, -1), addDays(T, -2)] : []) {
      const diary = (await apiFetch(`/v1/nutrition/day?date=${day}`).catch(() => null))?.body
      remaining.push(...(diary?.meals ?? []).map((meal) => meal.id).filter((id) => created.meals.includes(id)))
    }
    if (statuses.length) {
      check(
        statuses.every((status) => status === 200 || status === 204) && remaining.length === 0,
        'stage-8 stats test data removed',
        `${statuses.length} rows deleted`,
      )
    } else {
      ok('nothing to clean up')
    }
  }
}
