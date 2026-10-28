/**
 * Browser checks — stage 10: the unified «Сегодня» blocks below everything else (boards
 * Ffin-TodayMore / Dfin-TodayMore). Called by scripts/ui-check.mjs after the theme checks
 * and before the stage-9 accessibility audit (which stays last); removes what it creates
 * and leaves the appearance as it found it («Эволюция», dark).
 *
 *  - «План дня» = the server's day: one row per logged meal type (the time of its first
 *    meal and its kcal, done), today's planner tasks with a time (done / open; the first one
 *    still ahead is «Далее»), then the main meals not logged yet («пока не записан» +
 *    «Добавить ужин»); «Добавить» opens Питание's search and the log sheet starts on that meal.
 *  - «Повторить» = the first three of GET /foods/recents: one tap logs the food again with
 *    the grams it was last logged with, source=recent and the log sheet's meal type; the
 *    same client UUID replayed is the same row; «Отменить» removes it; a double tap logs
 *    once; offline the write waits in the queue (the day shows it at once) and is sent once.
 *  - The insight = GET /insights/today: the sentence of its metric and day, or nothing.
 *  - The look in both skins: shared cards in «Эволюция»; in «Наклейки» the lemon «Далее»
 *    sticker, mint done dots, ink lines, white tiles with a lemon «+», a lavender badge —
 *    light outlines in Ночь.
 *  - axe-core (WCAG 2.1 A/AA, serious + critical = 0) and the 48 px rule on Сегодня with the
 *    blocks in view: Эволюция dark and light, Наклейки Сирень and Ночь.
 *  - Screenshots s10t-*.png at 390 × 844 in $SHOTS (Сегодня scrolled to the blocks).
 */
import { randomUUID } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
const BLOCKING = new Set(['serious', 'critical'])
const MIN_TARGET = 48
const TAG = 'ui-check s10t'
const MEAL_RU = { breakfast: 'Завтрак', lunch: 'Обед', dinner: 'Ужин', snack: 'Перекус', other: 'Другое' }
const ADD_RU = { breakfast: 'Добавить завтрак', lunch: 'Добавить обед', dinner: 'Добавить ужин' }
const MAIN_MEALS = ['breakfast', 'lunch', 'dinner']
const SENTENCE_RU = {
  protein: { today: 'Белка больше, чем в среднем за 4 недели', yesterday: 'Вчера белка было больше, чем в среднем за 4 недели' },
  fiber: { today: 'Клетчатки больше, чем в среднем за 4 недели', yesterday: 'Вчера клетчатки было больше, чем в среднем за 4 недели' },
  water: { today: 'Воды больше, чем в среднем за 4 недели', yesterday: 'Вчера воды было больше, чем в среднем за 4 недели' },
  steps: { today: 'Шагов больше, чем в среднем за 4 недели', yesterday: 'Вчера шагов было больше, чем в среднем за 4 недели' },
}
const LEMON = 'rgb(255, 216, 77)'
const MINT = 'rgb(94, 224, 160)'
const LAVENDER = 'rgb(185, 162, 255)'
const NIGHT_TILE = 'rgb(36, 34, 47)'
const NIGHT_INK = 'rgb(245, 243, 255)'

function loadAxe() {
  try {
    const require = createRequire(new URL('../../web/package.json', import.meta.url))
    const version = JSON.parse(readFileSync(require.resolve('axe-core/package.json'), 'utf8')).version
    return { source: readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8'), version }
  } catch {
    return null
  }
}

const describeSource = `
  window.__s10tDescribe = (el) => {
    const part = (node) => {
      let out = node.tagName.toLowerCase()
      if (node.id) return out + '#' + node.id
      const testid = node.getAttribute('data-testid')
      if (testid) return out + '[data-testid="' + testid + '"]'
      const label = node.getAttribute('aria-label')
      if (label) return out + '[aria-label="' + label.slice(0, 40) + '"]'
      const classes = [...node.classList].filter((name) => !/[:\\[\\]/]/.test(name)).slice(0, 2)
      if (classes.length) out += '.' + classes.join('.')
      return out
    }
    const parts = []
    let node = el
    while (node && node !== document.body && parts.length < 3) {
      parts.unshift(part(node))
      if (node.id || node.getAttribute('data-testid')) break
      node = node.parentElement
    }
    return parts.join(' > ')
  }
`

/** Every Unicode space as a plain one (ru-RU formats 1 234 with U+00A0 / U+202F). */
const plain = (value) => String(value ?? '').replace(/[   ​]/g, ' ').replace(/\s+/g, ' ').trim()

export async function run(ctx) {
  const { context, WEB_URL, SHOTS, check, ok, bad } = ctx
  console.log('\n10b. stage 10 — Сегодня, ниже: «План дня», «Повторить», the insight')
  // a tab of its own (as the other stage-9/10 modules): the shared tab rests on about:blank meanwhile
  await ctx.page.goto('about:blank').catch(() => {})
  const page = await context.newPage()
  const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png` })
  const errors = []
  let offline = false
  page.on('console', (message) => {
    if (offline) return
    if (message.type() === 'error' && !/favicon|404 \(Not Found\)|Failed to load resource/i.test(message.text())) errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(String(error)))
  const axe = loadAxe()
  const totals = { audits: 0, blocking: 0, moderate: 0, minor: 0 }
  const report = []
  const created = { meals: new Set(), relogs: new Set(), tasks: [], steps: [] }

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
  const appearance = async (mode, stored) => {
    await page.evaluate(
      ({ mode, stored }) => {
        window.localStorage.setItem('dsos.theme', mode)
        if (stored) window.localStorage.setItem('dsos.appearance', JSON.stringify(stored))
        else window.localStorage.removeItem('dsos.appearance')
      },
      { mode, stored },
    )
  }
  const markSeen = async () => {
    const items = (await apiFetch('/v1/gamification/today')).body?.celebrate ?? []
    if (items.length) await apiFetch('/v1/gamification/achievements/seen', { method: 'POST', body: JSON.stringify({ ids: items.map((item) => item.id) }) })
  }
  const settle = async (ms = 400) => {
    await page.waitForTimeout(ms)
    await page.waitForFunction(() => !document.querySelector('main .skeleton'), null, { timeout: 10_000 }).catch(() => {})
    await page
      .evaluate(async () => {
        await document.fonts?.ready
        const deadline = Date.now() + 4000
        while (Date.now() < deadline) {
          const running = document
            .getAnimations()
            .filter((animation) => animation.playState === 'running' && animation.effect?.getComputedTiming?.().iterations !== Infinity)
          if (!running.length) return
          await new Promise((resolve) => setTimeout(resolve, 80))
        }
      })
      .catch(() => {})
  }
  /** Сегодня with the blocks loaded (they are a chunk of their own) and scrolled into view. */
  const openToday = async () => {
    await markSeen().catch(() => {})
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="day-plan"] ol').waitFor({ timeout: 15_000 })
    await settle()
    await scrollToBlocks()
  }
  const scrollToBlocks = async () => {
    await page.evaluate(() => {
      const plan = document.querySelector('[data-testid="day-plan"]')
      const header = document.querySelector('header.sticky')
      if (!plan) return
      const top = plan.getBoundingClientRect().top + window.scrollY - (header?.getBoundingClientRect().height ?? 0) - 12
      window.scrollTo(0, Math.max(0, top))
    })
    await page.waitForTimeout(250)
  }
  const inject = async () => {
    const missing = await page.evaluate(() => typeof window.axe === 'undefined' || typeof window.__s10tDescribe === 'undefined')
    if (missing) {
      await page.addScriptTag({ content: axe.source })
      await page.addScriptTag({ content: describeSource })
    }
  }
  const runAxe = async () => {
    await inject()
    return page.evaluate(
      async ({ tags }) => {
        const result = await window.axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] })
        return result.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          nodes: violation.nodes.map((node) => ({ target: node.target.join(' '), summary: (node.failureSummary ?? '').replace(/\s+/g, ' ').slice(0, 240) })),
        }))
      },
      { tags: AXE_TAGS },
    )
  }
  /** Operable controls under 48 × 48 px (the stage-9 rule; covered tab-bar targets count as small). */
  const smallTargets = async () => {
    await inject()
    return page.evaluate(
      ({ min }) => {
        const selector = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="link"], [role="tab"], [role="switch"], [role="checkbox"], [role="radio"], [tabindex]:not([tabindex="-1"])'
        const small = []
        for (const el of document.body.querySelectorAll(selector)) {
          if (el.closest('[role="dialog"], [aria-hidden="true"], [inert]') || el.disabled) continue
          const style = getComputedStyle(el)
          if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') continue
          const rect = el.getBoundingClientRect()
          if (rect.width <= 2 || rect.height <= 2) continue
          if (el.closest('nav.tabbar, header.sticky')) {
            const x = rect.left + rect.width / 2
            const y = rect.top + rect.height / 2
            const hit = x >= 0 && y >= 0 && x <= window.innerWidth && y <= window.innerHeight ? document.elementFromPoint(x, y) : null
            if (hit && !(hit === el || el.contains(hit))) {
              small.push(`${window.__s10tDescribe(el)} covered by ${window.__s10tDescribe(hit)}`)
              continue
            }
          }
          if (rect.width + 0.5 >= min && rect.height + 0.5 >= min) continue
          const parentText = (el.parentElement?.textContent ?? '').trim().length
          if (el.tagName === 'A' && style.display === 'inline' && parentText > (el.textContent ?? '').trim().length + 3) continue
          // the chart bars the stage-9 audit documents as exceptions
          if (el.closest('[data-testid="burned-chart"] li > button, [data-testid="steps-chart"] [role="listitem"] > button')) continue
          small.push(`${window.__s10tDescribe(el)} ${Math.round(rect.width)}×${Math.round(rect.height)}`)
        }
        const widened = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
        // the new blocks' own controls, measured on their own (they must be in the list above too)
        const own = [...document.querySelectorAll('[data-testid="today-more"] button, [data-testid="today-more"] a[href]')].map((el) => {
          const rect = el.getBoundingClientRect()
          return { w: Math.round(rect.width), h: Math.round(rect.height) }
        })
        return { small, widened, own }
      },
      { min: MIN_TARGET },
    )
  }
  const audit = async (name, screenshot) => {
    try {
      const targets = await smallTargets()
      check(
        targets.small.length === 0 && targets.own.length > 0 && targets.own.every((box) => box.w >= MIN_TARGET && box.h >= MIN_TARGET),
        `a11y ${name}: every target is at least 48 × 48 px (the blocks' own ${targets.own.length} too)`,
        targets.small.slice(0, 5).join(' | ') || targets.own.map((box) => `${box.w}×${box.h}`).join(' '),
      )
      if (targets.widened) bad(`a11y ${name}: nothing makes the page wider than the screen`)
      const violations = await runAxe()
      totals.audits += 1
      const blocking = violations.filter((violation) => BLOCKING.has(violation.impact))
      const lines = blocking.flatMap((violation) => violation.nodes.map((node) => `${violation.id} [${violation.impact}] ${node.target} — ${node.summary}`))
      totals.blocking += lines.length
      for (const violation of violations.filter((v) => !BLOCKING.has(v.impact))) totals[violation.impact === 'moderate' ? 'moderate' : 'minor'] += violation.nodes.length
      report.push({ state: name, violations })
      check(blocking.length === 0, `axe ${name}: no serious/critical violations`, lines.length ? `${lines.length} node(s): ${lines.slice(0, 3).join(' | ')}` : `${violations.length} moderate/minor rule(s)`)
      for (const line of lines) console.log(`         ${line}`)
      if (screenshot) await shot(screenshot)
    } catch (error) {
      bad(`a11y ${name}`, String(error?.message ?? error).split('\n')[0])
    }
  }
  const dayMeals = async () => (await apiFetch('/v1/nutrition/day')).body?.meals ?? []
  const logMeal = async (body) => {
    const id = randomUUID()
    const response = await apiFetch('/v1/nutrition/meals', { method: 'POST', body: JSON.stringify({ id, ...body }) })
    if (response.status === 200 || response.status === 201) created.meals.add(response.body.id)
    return response
  }
  const toastText = async () => plain(await page.locator('[data-testid="plan-toast"]').last().innerText().catch(() => ''))
  const waitToast = async (pattern) =>
    page
      .waitForFunction(
        (source) => [...document.querySelectorAll('[data-testid="plan-toast"]')].some((el) => new RegExp(source).test(el.textContent.replace(/[  ]/g, ' '))),
        pattern.source,
        { timeout: 8000 },
      )
      .then(() => true)
      .catch(() => false)

  try {
    if (!axe) {
      bad('axe-core is installed (web/node_modules/axe-core)', 'run `npm install` in web/')
      return
    }
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    await appearance('dark', null)

    // -------------------------------------------------------------------- //
    // seed: the day of the boards, as far as the clock allows (removed at the end)
    // -------------------------------------------------------------------- //
    const session = (await apiFetch('/v1/auth/session')).body
    const tz = session?.settings?.timezone ?? 'Europe/Moscow'
    const T = (await apiFetch('/v1/nutrition/day')).body?.date
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
        .formatToParts(new Date())
        .map((part) => [part.type, part.value]),
    )
    const nowMin = Number(parts.hour) * 60 + Number(parts.minute)
    const hhmm = (minutes) => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
    // a time of the boards when it has passed, else a little before now (never before midnight)
    const earlier = (minutes, index) => Math.max(1 + index, Math.min(minutes, nowMin - 10 - index * 5))
    const statuses = []
    const library = async (q, name) => {
      const items = (await apiFetch(`/v1/foods/search?q=${encodeURIComponent(q)}&limit=10`)).body?.items ?? []
      return items.find((item) => item.name_ru === name) ?? items[0]
    }
    const yogurt = await library('йогурт греческий', 'Йогурт греческий 2%')
    const buckwheat = await library('гречка отварная', 'Гречка отварная')
    const oatmeal = await library('овсянка на молоке', 'Каша овсяная на молоке')
    // the order makes them the three most recent: Овсянка, Гречка, Йогурт (as on the boards)
    const logged = (await dayMeals()).map((meal) => meal.meal_type)
    const lunchToo = !logged.includes('lunch') && !logged.includes('dinner') // keep one main meal missing
    for (const [food, grams, type, at, index] of [
      [yogurt, 184, 'snack', 16 * 60, 2],
      [buckwheat, 200, lunchToo ? 'lunch' : 'snack', 13 * 60 + 30, 1],
      [oatmeal, 276, 'breakfast', 8 * 60 + 10, 0],
    ]) {
      const response = await logMeal({
        meal_type: type,
        source: 'search',
        logged_at: `${T}T${hhmm(earlier(at, index))}:00`, // a local wall clock: the server places it in your timezone
        items: [{ food_id: food?.id, grams, match_method: 'manual' }],
      })
      statuses.push(response.status)
    }
    const task = async (body, done = false) => {
      const id = randomUUID()
      const response = await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ id, ...body }) })
      statuses.push(response.status)
      if (response.status === 200 || response.status === 201) created.tasks.push(id)
      if (done) statuses.push((await apiFetch(`/v1/planner/tasks/${id}/complete`, { method: 'POST', body: '{}' })).status)
    }
    const warmUp = earlier(7 * 60, 3)
    await task({ title: `Зарядка (${TAG})`, due_date: T, block: { date: T, start: hhmm(warmUp), end: hhmm(Math.min(warmUp + 20, 1439)) } }, true)
    const training = Math.ceil((nowMin + 30) / 5) * 5
    if (training + 60 <= 1439) {
      await task({ title: `Тренировка: ноги (${TAG})`, due_date: T, block: { date: T, start: hhmm(training), end: hhmm(training + 60) } })
    }
    const journal = Math.min(Math.ceil((nowMin + 150) / 5) * 5, 23 * 60 + 55)
    if (journal > nowMin + 20) await task({ title: `Дневник дня (${TAG})`, due_date: T, due_time: hhmm(journal), estimate_minutes: 5 })
    // steps: a week of 5 000 and 7 000 today — below the steps target, so no XP moves
    // (days that already have steps are left as they are; a day without any has source null)
    const health = (await apiFetch('/v1/health/today')).body
    const stepped = new Set(((await apiFetch('/v1/health/steps?days=10')).body?.days ?? []).filter((d) => d.source != null).map((d) => d.date))
    for (let back = 1; back <= 8; back += 1) {
      const date = new Date(Date.parse(`${T}T12:00:00Z`) - back * 86_400_000).toISOString().slice(0, 10)
      if (stepped.has(date)) continue
      const response = await apiFetch(`/v1/health/steps/${date}`, { method: 'PUT', body: JSON.stringify({ steps: 5000 }) })
      statuses.push(response.status)
      if (response.status === 200) created.steps.push(date)
    }
    if (health?.day && health.day.source == null) {
      const response = await apiFetch(`/v1/health/steps/${T}`, { method: 'PUT', body: JSON.stringify({ steps: 7000 }) })
      statuses.push(response.status)
      if (response.status === 200) created.steps.push(T)
    }
    check(
      statuses.every((status) => status === 200 || status === 201) && !!yogurt && !!buckwheat && !!oatmeal,
      'seeded the day of the boards: three library meals, a done / a coming / a later task, a week of steps',
      `${statuses.length} writes on ${T} at ${hhmm(nowMin)} (${tz})`,
    )

    // -------------------------------------------------------------------- //
    // 1. «План дня» = the server's day, rows in order, «Далее», the meals still to log
    // -------------------------------------------------------------------- //
    await openToday()
    const expectPlan = async () => {
      const day = (await apiFetch('/v1/nutrition/day')).body
      const view = (await apiFetch('/v1/planner/views/today')).body
      const clock = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: tz })
      const kcal = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 })
      const groups = new Map()
      for (const meal of day.meals) {
        const group = groups.get(meal.meal_type)
        if (!group) groups.set(meal.meal_type, { first: meal, kcal: meal.kcal })
        else {
          if (Date.parse(meal.logged_at) < Date.parse(group.first.logged_at)) group.first = meal
          group.kcal += meal.kcal
        }
      }
      const rows = [...groups].map(([type, group]) => ({
        kind: 'meal',
        state: 'done',
        at: Date.parse(group.first.logged_at),
        time: plain(clock.format(new Date(group.first.logged_at))),
        words: [MEAL_RU[type], `${plain(kcal.format(group.kcal))} ккал`],
      }))
      const timed = (item) =>
        item.block && item.block.date === view.date
          ? { at: Date.parse(item.block.start_at), time: item.block.start, until: Date.parse(item.block.end_at), block: true }
          : item.due_date === view.date && item.due_time && item.due_at
            ? { at: Date.parse(item.due_at), time: item.due_time, until: Date.parse(item.due_at), block: false }
            : null
      const now = Date.now()
      const open = [...view.scheduled, ...view.today]
        .map((item) => ({ item, place: timed(item) }))
        .filter((entry) => entry.place)
        .sort((a, b) => a.place.at - b.place.at)
      const next = open.find((entry) => (entry.place.block ? entry.place.until > now : !entry.item.overdue))
      for (const entry of open) rows.push({ kind: 'task', state: entry === next ? 'next' : 'open', at: entry.place.at, time: entry.place.time, words: [entry.item.title] })
      for (const item of view.done) {
        const place = timed(item)
        if (place) rows.push({ kind: 'task', state: 'done', at: place.at, time: place.time, words: [item.title] })
      }
      rows.sort((a, b) => a.at - b.at)
      for (const meal of MAIN_MEALS) if (!groups.has(meal)) rows.push({ kind: 'missing', state: 'missing', time: '', words: [MEAL_RU[meal], 'пока не записан'], meal })
      return rows
    }
    const readPlan = () =>
      page.$$eval('[data-testid="day-plan"] [data-testid="plan-row"]', (rows) =>
        rows.map((row) => ({
          kind: row.dataset.kind,
          state: row.dataset.state,
          time: (row.querySelector('[data-testid="plan-time"]')?.textContent ?? '').trim(),
          text: row.textContent.replace(/[   ]/g, ' ').replace(/\s+/g, ' ').trim(),
          add: row.querySelector('[data-testid="plan-add"]')?.getAttribute('aria-label') ?? null,
          meal: row.querySelector('[data-testid="plan-add"]')?.dataset.meal ?? null,
        })),
      )
    const want = await expectPlan()
    const got = await readPlan()
    const mismatch = want
      .map((row, index) => {
        const ui = got[index]
        if (!ui) return `#${index} missing`
        if (ui.kind !== row.kind || ui.state !== row.state || ui.time !== row.time) return `#${index} ${ui.kind}/${ui.state}/${ui.time} ≠ ${row.kind}/${row.state}/${row.time}`
        const lost = row.words.filter((word) => !ui.text.includes(word))
        return lost.length ? `#${index} «${ui.text}» lacks ${lost.join(', ')}` : null
      })
      .filter(Boolean)
    check(
      got.length === want.length && mismatch.length === 0,
      '«План дня» = the server\'s day: meals by type (time of the first, kcal), timed tasks in order, the main meals still to log last',
      mismatch.length ? mismatch.slice(0, 3).join(' | ') : got.map((row) => `${row.time || '—'} ${row.state}`).join(' · '),
    )
    const listName = await page.evaluate(() => {
      const list = document.querySelector('[data-testid="day-plan"] ol')
      return document.getElementById(list?.getAttribute('aria-labelledby') ?? '')?.textContent ?? ''
    })
    check(listName === 'План дня', 'the timeline is a list named by its heading «План дня»', listName)
    const nextRow = want.find((row) => row.state === 'next')
    if (nextRow) {
      const shown = await page.evaluate(() => {
        const row = document.querySelector('[data-testid="plan-row"][data-state="next"]')
        return row ? { text: row.textContent, bg: getComputedStyle(row).backgroundColor } : null
      })
      check(
        !!shown && /Далее/.test(shown.text) && shown.bg !== 'rgba(0, 0, 0, 0)',
        '«Далее»: the first open task still ahead is emphasised (its own surface, the label)',
        shown ? `${plain(shown.text)} · ${shown.bg}` : 'no next row',
      )
    } else {
      ok('«Далее»: no open task is still ahead at this hour — nothing is emphasised', hhmm(nowMin))
    }
    const done = got.filter((row) => row.state === 'done')
    check(
      done.length > 0 && done.every((row) => /, (записано|выполнено)/.test(row.text)),
      'done rows say so to screen readers («записано» / «выполнено»), the dots stay decorative',
      `${done.length} done`,
    )

    // «Добавить» → Питание's search → the log sheet starts on that meal; nothing is saved
    const missing = got.find((row) => row.kind === 'missing')
    if (missing) {
      check(missing.add === ADD_RU[missing.meal], `«Добавить» is named for its meal («${ADD_RU[missing.meal]}»)`, String(missing.add))
      const mealsBefore = (await dayMeals()).length
      await page.locator(`[data-testid="plan-add"][data-meal="${missing.meal}"]`).click()
      await page.waitForURL(/\/food/, { timeout: 8000 })
      const search = page.locator('input[placeholder*="Поиск"]').first()
      await search.waitFor({ timeout: 10_000 })
      await search.fill('гречка отварная')
      await page.locator('section div.surface-sunken > button').first().waitFor({ timeout: 8000 })
      await page.locator('section div.surface-sunken > button').first().click()
      await page.locator('[role="dialog"]').last().waitFor({ timeout: 8000 })
      await page.waitForTimeout(400)
      const chosen = await page.locator('[role="dialog"] [role="tab"][aria-selected="true"]').first().innerText().catch(() => '')
      check(chosen.trim() === MEAL_RU[missing.meal], `«Добавить» opens Питание's search; the log sheet starts on «${MEAL_RU[missing.meal]}»`, chosen.trim())
      await page.keyboard.press('Escape')
      await page.waitForTimeout(400)
      check((await dayMeals()).length === mealsBefore, '…and nothing is saved until the sheet is confirmed')
    } else {
      ok('«Добавить»: every main meal is logged today — no «пока не записан» row to open', logged.join(', '))
    }

    // -------------------------------------------------------------------- //
    // 2. «Повторить»: the three most recent foods, one tap logs again (offline too)
    // -------------------------------------------------------------------- //
    await openToday()
    const recents = ((await apiFetch('/v1/foods/recents')).body?.items ?? []).filter((item) => item.food || item.recipe).slice(0, 3)
    const portion = (recent) => {
      const source = recent.food ?? recent.recipe
      const fallback = recent.food ? (recent.food.favorite_grams ?? recent.food.default_serving_g ?? 100) : recent.recipe.portion_size_g || 100
      const grams = Math.round(recent.last_grams || fallback)
      // as the tile: kcal of the portion to 0.01, then rounded for display
      const kcal = Math.round(Math.round(source.kcal_per_100g * (grams / 100) * 100) / 100)
      return { name: recent.food ? (recent.food.name_ru ?? recent.food.name_en) : recent.recipe.name, grams, kcal }
    }
    const expected = recents.map(portion)
    const kcalText = (value) => plain(new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(value))
    const tiles = await page.$$eval('[data-testid="repeat-food"]', (buttons) =>
      buttons.map((button) => ({ label: button.getAttribute('aria-label') ?? '', text: button.textContent.replace(/[  ]/g, ' ') })),
    )
    const group = await page.evaluate(() => document.querySelector('[data-testid="repeat-foods"] [role="group"]')?.getAttribute('aria-label') ?? '')
    check(
      tiles.length === expected.length &&
        expected.length > 0 &&
        expected.every((item, index) => plain(tiles[index].label) === `Записать снова: ${item.name}, ${kcalText(item.kcal)} ккал` && tiles[index].text.includes(item.name)) &&
        group === 'Повторить в одно касание',
      '«Повторить» = the first three of GET /foods/recents, each named «Записать снова: …, N ккал», in the group «Повторить в одно касание»',
      tiles.map((tile) => plain(tile.label)).join(' | '),
    )
    if (expected.length) {
      const first = recents[0]
      const expectType = await page.evaluate(() => {
        const hour = new Date().getHours()
        return hour < 11 ? 'breakfast' : hour < 16 ? 'lunch' : hour < 22 ? 'dinner' : 'snack'
      })
      // one tap → logged now: source=recent, the same food and grams, the log sheet's meal type
      let before = new Set((await dayMeals()).map((meal) => meal.id))
      await page.locator('[data-testid="repeat-food"]').first().click()
      const toasted = await waitToast(/записано/)
      const toast = await toastText()
      let fresh = (await dayMeals()).filter((meal) => !before.has(meal.id))
      for (const meal of fresh) created.relogs.add(meal.id)
      const item = fresh[0]?.items?.[0]
      check(
        fresh.length === 1 &&
          fresh[0].source === 'recent' &&
          fresh[0].meal_type === expectType &&
          (first.food ? item?.food_id === first.food.id : item?.recipe_id === first.recipe.id) &&
          Math.round(item?.grams ?? 0) === expected[0].grams,
        `one tap logs «${expected[0].name}» again: ${expected[0].grams} g, source=recent, «${MEAL_RU[expectType]}» (the log sheet's meal type)`,
        fresh.map((meal) => `${meal.source}/${meal.meal_type}/${meal.items?.[0]?.grams} g`).join(', ') || 'nothing logged',
      )
      check(
        toasted && toast.includes(`${expected[0].name} · ${kcalText(expected[0].kcal)} ккал — записано`) && /Отменить/.test(toast),
        'the toast confirms it and offers «Отменить»',
        toast,
      )
      // the client UUID makes a replay the same row (the offline queue's guarantee)
      if (fresh.length === 1) {
        const replay = await apiFetch('/v1/nutrition/meals', {
          method: 'POST',
          body: JSON.stringify({ id: fresh[0].client_uuid, meal_type: expectType, source: 'recent', items: [{ ...(first.food ? { food_id: first.food.id } : { recipe_id: first.recipe.id }), grams: expected[0].grams, match_method: 'manual' }] }),
        })
        const count = (await dayMeals()).filter((meal) => meal.client_uuid === fresh[0].client_uuid).length
        check(replay.body?.id === fresh[0].id && count === 1, 'replaying the same client UUID answers the same meal (no duplicate)', `${replay.status} · ${count} row`)
      }
      // «Отменить» removes it
      await page.locator('[data-testid="plan-toast"] button', { hasText: 'Отменить' }).last().click()
      await page.waitForTimeout(1200)
      const remaining = (await dayMeals()).filter((meal) => fresh.some((row) => row.id === meal.id))
      check(fresh.length === 1 && remaining.length === 0, '«Отменить» removes the re-logged meal', `${remaining.length} left`)
      // a double tap logs once
      await page.waitForFunction(() => !document.querySelector('[data-testid="repeat-food"][aria-busy="true"]'), null, { timeout: 5000 }).catch(() => {})
      before = new Set((await dayMeals()).map((meal) => meal.id))
      await page.locator('[data-testid="repeat-food"]').first().dblclick()
      await page.waitForTimeout(1500)
      fresh = (await dayMeals()).filter((meal) => !before.has(meal.id))
      for (const meal of fresh) created.relogs.add(meal.id)
      check(fresh.length === 1, 'a double tap logs once (the tile ignores taps while its write is on its way)', `${fresh.length} meal(s)`)
      // offline: queued with a clear message, in the day at once, sent once when the network is back
      await page.waitForTimeout(600)
      before = new Set((await dayMeals()).map((meal) => meal.id))
      offline = true
      await (ctx.setOffline ? ctx.setOffline(true) : context.setOffline(true))
      await page.locator('[data-testid="repeat-food"]').first().click()
      const queued = await waitToast(/запишется, когда появится сеть/)
      const pendingRow = await page
        .locator('[data-testid="meal-row"][data-pending="true"]')
        .first()
        .waitFor({ timeout: 5000 })
        .then(() => true)
        .catch(() => false)
      const planPending = await page.evaluate(() => /ждёт отправки/.test(document.querySelector('[data-testid="day-plan"]')?.textContent ?? ''))
      check(queued && pendingRow && planPending, 'offline: the tap is queued («запишется, когда появится сеть»), the day and «План дня» show it at once («ждёт отправки»)')
      await (ctx.setOffline ? ctx.setOffline(false) : context.setOffline(false))
      offline = false
      await page.evaluate(() => window.dispatchEvent(new Event('online')))
      let sent = []
      for (let attempt = 0; attempt < 20; attempt += 1) {
        await page.waitForTimeout(500)
        sent = (await dayMeals()).filter((meal) => !before.has(meal.id))
        if (sent.length) break
      }
      await page.waitForTimeout(1500)
      sent = (await dayMeals()).filter((meal) => !before.has(meal.id))
      for (const meal of sent) created.relogs.add(meal.id)
      check(sent.length === 1 && sent[0].source === 'recent', 'back online: the queued re-log reached the server exactly once', `${sent.length} meal(s)`)
    }
    // the re-logs go again, so the day below is the seeded one (a main meal still to log)
    for (const id of created.relogs) {
      const status = (await apiFetch(`/v1/nutrition/meals/${id}`, { method: 'DELETE' })).status
      if (status === 200 || status === 404) created.relogs.delete(id)
    }

    // -------------------------------------------------------------------- //
    // 3. the insight = GET /insights/today
    // -------------------------------------------------------------------- //
    await openToday()
    const insight = (await apiFetch('/v1/insights/today')).body
    const shownInsight = await page.evaluate(() => {
      const section = document.querySelector('[data-testid="today-insight"]')
      return section ? { metric: section.dataset.metric, day: section.dataset.day, text: section.textContent.trim(), name: section.getAttribute('aria-label') } : null
    })
    if (insight?.insight) {
      const sentence = SENTENCE_RU[insight.insight.metric][insight.insight.day]
      check(
        shownInsight?.metric === insight.insight.metric && shownInsight?.day === insight.insight.day && shownInsight?.text === sentence && shownInsight?.name === 'Наблюдение',
        'the insight = GET /insights/today (the sentence of its metric and day, region «Наблюдение»)',
        `${insight.insight.metric}/${insight.insight.day}: ${insight.insight.value} vs ${insight.insight.average} over ${insight.insight.days_with_data} days — «${shownInsight?.text ?? 'not shown'}»`,
      )
    } else {
      check(shownInsight === null, 'no insight from the server → nothing is shown', JSON.stringify(insight))
    }
    check(!/калори|ккал|меньше/i.test(shownInsight?.text ?? ''), 'the insight never speaks of calories or of «less»', shownInsight?.text ?? '—')

    // -------------------------------------------------------------------- //
    // 4. the look in both skins + axe and 48 px targets, screenshots
    // -------------------------------------------------------------------- //
    const look = () =>
      page.evaluate(() => {
        const style = (selector, ...props) => {
          const el = document.querySelector(selector)
          if (!el) return null
          const computed = getComputedStyle(el)
          return Object.fromEntries(props.map((prop) => [prop, computed[prop]]))
        }
        return {
          card: style('[data-testid="day-plan"] .sk-plan', 'backgroundColor', 'borderTopWidth', 'borderTopColor'),
          next: style('[data-testid="plan-row"][data-state="next"]', 'backgroundColor', 'borderTopWidth'),
          doneDot: style('[data-testid="plan-row"][data-state="done"] .sk-plan-dot', 'backgroundColor'),
          line: style('[data-testid="day-plan"] .sk-plan-line', 'borderLeftColor'),
          tile: style('[data-testid="repeat-food"]', 'borderTopWidth', 'borderTopLeftRadius', 'backgroundColor'),
          plus: style('[data-testid="repeat-food"] .sk-repeat-plus', 'backgroundColor'),
          badge: style('[data-testid="today-insight"] .sk-insight-badge', 'backgroundColor'),
          font: getComputedStyle(document.body).fontFamily,
        }
      })
    await appearance('dark', null)
    await openToday()
    const evolution = await look()
    check(
      evolution.card?.borderTopWidth === '1px' && evolution.tile?.borderTopWidth === '1px' && evolution.next?.backgroundColor !== LEMON && evolution.doneDot?.backgroundColor !== MINT && !/Rubik/.test(evolution.font),
      'Эволюция: the blocks are the shared cards of the stage 1–9 look (1 px borders, the accent, Inter)',
      JSON.stringify({ card: evolution.card, next: evolution.next?.backgroundColor, dot: evolution.doneDot?.backgroundColor }),
    )
    await audit('Сегодня · новые блоки (Эволюция, dark)', 's10t-01-today-more-evolution-dark')
    await appearance('light', null)
    await openToday()
    await audit('Сегодня · новые блоки (Эволюция, light)', 's10t-02-today-more-evolution-light')

    await appearance('light', { skin: 'stickers', ground: 'lilac', tilt: true, shadow: 'hard' })
    await openToday()
    const lilac = await look()
    check(
      lilac.card?.borderTopWidth === '2px' &&
        (!lilac.next || (lilac.next.backgroundColor === LEMON && lilac.next.borderTopWidth === '2px')) &&
        lilac.doneDot?.backgroundColor === MINT &&
        lilac.line?.borderLeftColor === 'rgb(17, 17, 17)' &&
        (!lilac.tile || (lilac.tile.borderTopWidth === '2px' && lilac.tile.borderTopLeftRadius === '14px' && lilac.plus?.backgroundColor === LEMON)) &&
        (!lilac.badge || lilac.badge.backgroundColor === LAVENDER) &&
        /Rubik/.test(lilac.font),
      'Наклейки (Сирень): «Далее» a lemon sticker, mint done dots, ink lines, white tiles with a lemon «+», the lavender badge, Rubik',
      JSON.stringify({ next: lilac.next, dot: lilac.doneDot?.backgroundColor, line: lilac.line?.borderLeftColor, tile: lilac.tile, plus: lilac.plus?.backgroundColor, badge: lilac.badge?.backgroundColor }),
    )
    await audit('Сегодня · новые блоки (Наклейки, Сирень)', 's10t-03-today-more-stickers-lilac')

    await appearance('dark', { skin: 'stickers', ground: 'lilac', tilt: true, shadow: 'hard' })
    await openToday()
    const night = await look()
    check(
      night.card?.backgroundColor === NIGHT_TILE &&
        night.card?.borderTopColor === NIGHT_INK &&
        night.line?.borderLeftColor === NIGHT_INK &&
        (!night.next || night.next.backgroundColor === LEMON) &&
        night.doneDot?.backgroundColor === MINT,
      'Наклейки (Ночь): night tiles with light outlines and lines; the stickers stay bright',
      JSON.stringify({ card: night.card, line: night.line?.borderLeftColor, next: night.next?.backgroundColor }),
    )
    await audit('Сегодня · новые блоки (Наклейки, Ночь)', 's10t-04-today-more-stickers-night')
  } catch (error) {
    bad('stage 10 Сегодня checks', String(error?.message ?? error).split('\n')[0])
  } finally {
    if (offline) await context.setOffline(false).catch(() => {})
    try {
      writeFileSync(`${SHOTS}/s10t-axe.json`, JSON.stringify({ totals, report }, null, 2))
    } catch {
      /* the screenshots directory is optional */
    }
    ok('axe summary (stage 10 Сегодня)', `${totals.audits} audits: ${totals.blocking} serious/critical, ${totals.moderate} moderate, ${totals.minor} minor node(s) — ${SHOTS}/s10t-axe.json`)
    check(errors.length === 0, 'no console errors during the Сегодня checks', errors.slice(0, 2).join(' | '))
    // leave the device as the other modules expect it: «Эволюция», dark; remove what this module made
    await appearance('dark', null).catch(() => {})
    const removed = []
    for (const id of [...created.meals, ...created.relogs]) {
      const status = (await apiFetch(`/v1/nutrition/meals/${id}`, { method: 'DELETE' }).catch(() => ({ status: 0 }))).status
      removed.push(status === 404 ? 200 : status) // an undone re-log is gone already
    }
    for (const id of created.tasks) removed.push((await apiFetch(`/v1/planner/tasks/${id}`, { method: 'DELETE' }).catch(() => ({ status: 0 }))).status)
    for (const date of created.steps) removed.push((await apiFetch(`/v1/health/steps/${date}`, { method: 'DELETE' }).catch(() => ({ status: 0 }))).status)
    const reset = await page.evaluate(() => [window.localStorage.getItem('dsos.theme'), window.localStorage.getItem('dsos.appearance')]).catch(() => ['?', '?'])
    check(
      removed.every((status) => status === 200 || status === 204) && reset[0] === 'dark' && reset[1] === null,
      'Сегодня test data removed, the appearance back to «Эволюция» (dark)',
      `${removed.length} rows deleted; ${reset.join(' / ')}`,
    )
    await page.close().catch(() => {})
    await ctx.page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' }).catch(() => {})
  }
}
