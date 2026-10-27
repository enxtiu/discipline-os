/**
 * Browser checks — stage 8 gamification on Today and /stats/achievements: the
 * day score card against the API, the score explanation sheet (zone, floor,
 * burned calories), score and XP following a meal + water + a task, the
 * achievement celebration (once, reduced motion), claiming a quest (idempotent),
 * the achievements screen (unlocked, locked with progress, masked secrets).
 * Called by scripts/ui-check.mjs. Everything this module logs is removed at the
 * end; XP from a claimed quest or an unlocked achievement is permanent by design.
 */

/** Unicode spaces from ru-RU number formatting → plain spaces. */
const norm = (value) => String(value ?? '').replace(/[   ​]/g, ' ')
const ru = (value) => norm(new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(value))

export async function run(ctx) {
  const { page, WEB_URL, check, ok, bad, shot } = ctx
  console.log('\n7¹⁴. stage 8 day score, XP, streaks, quests, achievements')

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
  const game = async () => (await apiFetch('/v1/gamification/today')).body
  const nutritionDay = async () => (await apiFetch('/v1/nutrition/day')).body
  /** Do something and wait for the API call it triggers. */
  const after = async (method, fragment, action, timeout = 10_000) => {
    const [response] = await Promise.all([
      page.waitForResponse((r) => r.request().method() === method && r.url().includes(fragment), { timeout }),
      action(),
    ])
    return response
  }
  /** Poll a condition in Node (the card refetches asynchronously). */
  const until = async (condition, timeout = 8000) => {
    const end = Date.now() + timeout
    while (Date.now() < end) {
      if (await condition().catch(() => false)) return true
      await page.waitForTimeout(200)
    }
    return false
  }
  const card = page.locator('[data-testid="day-score-card"]')
  const openToday = async () => {
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    await card.locator('[data-testid="day-score-value"]').waitFor({ timeout: 15_000 })
  }
  /** What the card shows, read from its DOM. */
  const readCard = async () => ({
    score: Number(norm(await card.locator('[data-testid="day-score-value"]').innerText()).replace(/\D/g, '')),
    rings: Object.fromEntries(
      await card
        .locator('[data-testid="score-ring"]')
        .evaluateAll((els) => els.map((el) => [el.getAttribute('data-ring'), Number(el.getAttribute('data-value'))])),
    ),
    xpToday: norm(await card.locator('[data-testid="day-xp-today"]').innerText()),
    xpTotal: norm(await card.locator('[data-testid="day-xp-total"]').innerText()),
    level: Number(await card.locator('[data-testid="day-level"]').getAttribute('data-level')),
    levelText: norm(await card.locator('[data-testid="day-level"]').innerText()),
    provisional: (await card.locator('[data-testid="score-provisional"]').count()) > 0,
    streaks: await card
      .locator('[data-testid="streak-chip"]')
      .evaluateAll((els) => els.map((el) => `${el.getAttribute('data-kind')}:${el.getAttribute('data-current')}`)),
    quests: await card
      .locator('[data-testid="today-quests"] [data-testid="quest-row"]')
      .evaluateAll((els) => els.map((el) => `${el.getAttribute('data-quest')}:${el.getAttribute('data-status')}`)),
  })
  const sameRings = (shown, api) => api.rings.every((ring) => Math.abs((shown[ring.key] ?? -1) - ring.value) < 1e-9)
  const cardQuests = (api) =>
    api.quests.filter((quest) => quest.kind === 'daily' || quest.status === 'completed').map((q) => `${q.code}:${q.status}`)

  // the module's own console errors (the harness checks the whole run as well)
  const errors = []
  const onConsole = (message) => {
    if (message.type() === 'error' && !/favicon|404 \(Not Found\)/i.test(message.text())) errors.push(message.text())
  }
  const onPageError = (error) => errors.push(String(error))
  page.on('console', onConsole)
  page.on('pageerror', onPageError)

  const created = { meals: [], water: [], tasks: [], journal: false, steps: false, sleep: false }
  let day = null
  let foodBefore = null
  let waterStart = null

  try {
    // 1. the card shows exactly what the API computed
    await openToday()
    const start = await game()
    day = start.date
    const dayStart = await nutritionDay()
    foodBefore = dayStart?.totals ?? null
    waterStart = dayStart?.water_ml ?? null
    const waterBefore = waterStart ?? 0
    const shown = await readCard()
    const lvl = start.xp.level
    check(
      shown.score === Math.round(start.score.value) &&
        sameRings(shown.rings, start) &&
        shown.xpToday.includes(`+${ru(start.xp.today)} XP`) &&
        shown.xpTotal.startsWith(
          lvl.next_min_xp != null ? `${ru(start.xp.total)} / ${ru(lvl.next_min_xp)} XP` : `${ru(start.xp.total)}`,
        ) &&
        shown.level === lvl.level &&
        shown.levelText.includes(lvl.title_ru),
      'Today card = GET /gamification/today (score, 3 rings, XP today/total, level and title)',
      `score ${shown.score}, XP +${start.xp.today}/${start.xp.total}, ур. ${lvl.level} «${lvl.title_ru}»`,
    )
    const apiStreaks = start.streaks.filter((s) => s.current > 0).map((s) => `${s.kind}:${s.current}`)
    check(
      JSON.stringify(shown.streaks) === JSON.stringify(apiStreaks) &&
        JSON.stringify(shown.quests) === JSON.stringify(cardQuests(start)),
      'card streak flames and today’s quests = the API',
      `${apiStreaks.join(', ') || 'no streaks yet'} · ${cardQuests(start).join(', ')}`,
    )
    check(start.score.provisional === true && shown.provisional, 'today’s score is labelled «предварительно» (provisional)')

    // 2. water from Today refreshes the card by itself; a meal and a task move score and XP
    const waterResponse = await after('GET', '/v1/gamification/today', async () => {
      const posted = await after('POST', '/v1/nutrition/water', () =>
        page.getByRole('button', { name: /\+250 мл/ }).first().click(),
      )
      const body = await posted.json().catch(() => null)
      if (body?.id) created.water.push(body.id)
    })
    const afterWater = await waterResponse.json()
    const refreshed = await until(async () => sameRings((await readCard()).rings, afterWater))
    const ringMoved = afterWater.rings.find((r) => r.key === 'nutrition').value > start.rings.find((r) => r.key === 'nutrition').value
    check(
      refreshed && ringMoved && created.water.length === 1,
      '+250 мл on Today → the card refetches by itself, nutrition ring grows',
      `${start.rings[0].value} → ${afterWater.rings[0].value}`,
    )

    const meal = await apiFetch('/v1/nutrition/meals', {
      method: 'POST',
      body: JSON.stringify({
        meal_type: 'snack',
        items: [{ name: 'Творог 5% (ui-check)', grams: 100, kcal_per_100g: 121, protein_per_100g: 17, fat_per_100g: 5, carbs_per_100g: 3 }],
      }),
    })
    if (meal.status === 201) created.meals.push(meal.body.id)
    const task = await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ title: 'Задача для оценки (ui-check)', priority: 1 }) })
    if (task.status === 201) created.tasks.push(task.body.id)
    const done = task.status === 201 ? await apiFetch(`/v1/planner/tasks/${task.body.id}/complete`, { method: 'POST', body: '{}' }) : task
    await openToday() // a refetch
    const moved = await game()
    const detail = (snapshot, key) => snapshot.score.components.find((c) => c.key === key)?.detail ?? {}
    const shownMoved = await readCard()
    check(
      meal.status === 201 &&
        done.status === 200 &&
        Math.round(detail(moved, 'kcal').amount) === Math.round(detail(afterWater, 'kcal').amount + 121) &&
        detail(moved, 'water').amount === waterBefore + 250 &&
        detail(moved, 'tasks').done === (detail(start, 'tasks').done ?? 0) + 1,
      'meal +121 kcal, water +250 ml and a done task reach the score components',
      `kcal ${detail(start, 'kcal').amount} → ${detail(moved, 'kcal').amount}, water ${detail(moved, 'water').amount} ml, tasks done ${detail(moved, 'tasks').done}`,
    )
    check(
      moved.score.value !== start.score.value && moved.xp.today >= start.xp.today + 12,
      'score and XP move (P1 task +12 XP at least)',
      `score ${start.score.value} → ${moved.score.value}, XP today ${start.xp.today} → ${moved.xp.today}`,
    )
    check(
      shownMoved.score === Math.round(moved.score.value) &&
        sameRings(shownMoved.rings, moved) &&
        shownMoved.xpToday.includes(`+${ru(moved.xp.today)} XP`),
      'after the refetch the card shows the new score, rings and XP',
      `${shownMoved.score} · ${shownMoved.xpToday}`,
    )

    // 3. celebration: unseen achievements → one sheet (reduced motion respected) → seen, never again
    const celebrate = moved.celebrate ?? []
    if (!celebrate.length) {
      ok('no unseen achievements on this database — celebration already shown on an earlier run')
    } else {
      check(
        (await card.locator('[data-testid="celebrate-pill"]').count()) === 1,
        'the card announces new achievements (pill)',
        celebrate.map((item) => item.name_ru).join(', '),
      )
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await card.locator('[data-testid="day-score-button"]').scrollIntoViewIfNeeded()
      const celebration = page.locator('[data-testid="celebration"]')
      const appeared = await celebration
        .waitFor({ timeout: 8000 })
        .then(() => true)
        .catch(() => false)
      const order = ['common', 'rare', 'epic', 'legendary']
      const lead = [...celebrate].sort(
        (a, b) => order.indexOf(b.rarity) - order.indexOf(a.rarity) || (b.unlocked_at ?? '').localeCompare(a.unlocked_at ?? ''),
      )[0]
      const sheetBody = appeared ? norm(await page.locator('[role="dialog"]').last().innerText()) : ''
      const rarity = { common: 'обычное', rare: 'редкое', epic: 'эпическое', legendary: 'легендарное' }[lead.rarity]
      check(
        appeared &&
          sheetBody.includes(lead.name_ru) &&
          sheetBody.toLowerCase().includes(rarity) &&
          sheetBody.includes(`+${lead.xp_reward} XP`) &&
          (lead.description_ru ? sheetBody.includes(lead.description_ru) : true) &&
          (await page.locator('[data-testid="celebration-more"]').count()) === celebrate.length - 1,
        'score in view → the celebration sheet: badge, name, description, +XP, rarity, the rest listed',
        `${lead.name_ru} +${lead.xp_reward} XP (${lead.rarity}), ${celebrate.length} new`,
      )
      check(
        (await celebration.getAttribute('data-motion').catch(() => null)) === 'reduced',
        'prefers-reduced-motion: the badge does not animate',
      )
      await shot('52-celebration')
      const seen = appeared
        ? await after('POST', '/v1/gamification/achievements/seen', () => page.locator('[data-testid="celebration-ok"]').click())
        : null
      const seenBody = seen ? await seen.json().catch(() => null) : null
      await page.emulateMedia({ reducedMotion: null })
      const left = (await game()).celebrate ?? []
      check(seen?.status() === 200 && seenBody?.ok === true && left.length === 0, 'closing it → POST /achievements/seen, nothing left to celebrate')
      await openToday()
      await card.locator('[data-testid="day-score-button"]').scrollIntoViewIfNeeded()
      await page.waitForTimeout(2000)
      check(
        (await page.locator('[data-testid="celebration"]').count()) === 0 &&
          (await card.locator('[data-testid="celebrate-pill"]').count()) === 0,
        'after a reload the celebration does not come back',
      )
    }

    // 4. the explanation sheet: every component, the zone, the floor, burned calories
    await card.scrollIntoViewIfNeeded()
    await shot('50-today-score-card')
    await card.locator('[data-testid="day-score-button"]').click()
    const sheet = page.locator('[data-testid="score-sheet"]')
    await sheet.waitFor({ timeout: 8000 })
    const rows = await sheet
      .locator('[data-testid="score-component"]')
      .evaluateAll((els) => els.map((el) => `${el.getAttribute('data-key')}:${el.getAttribute('data-excluded')}`))
    check(
      JSON.stringify(rows) === JSON.stringify((await game()).score.components.map((c) => `${c.key}:${c.excluded}`)),
      'score sheet lists all 8 components, excluded ones marked',
      rows.filter((row) => row.endsWith('true')).join(', ') || 'none excluded',
    )
    const current = await game()
    const kcal = current.score.components.find((c) => c.key === 'kcal')?.detail ?? {}
    const sheetText = norm(await sheet.innerText())
    const zoneLine = `Цель ${ru(kcal.target)} ккал — зона ${ru(kcal.zone_low)}–${ru(kcal.zone_high)} ккал`
    check(sheetText.includes(zoneLine), 'sheet explains the kcal zone from the server', zoneLine)
    check(
      sheetText.includes(`безопасного минимума ${ru(kcal.floor)} ккал`) &&
        sheetText.includes('даёт за калории 0') &&
        sheetText.includes('Сожжённые калории') &&
        sheetText.includes('никогда не добавляются к еде') &&
        sheetText.includes('прогресс, а не оценка'),
      'sheet: below the floor scores 0, burned kcal never count as food, today is progress',
    )
    await shot('51-score-sheet')
    await page.locator('[role="dialog"]').getByRole('button', { name: 'Close' }).click()
    await sheet.waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})

    // 5. claim a completed quest: +XP toast, XP grows once; a second claim is a no-op
    const completeQuest = {
      daily_water: async (quest) => {
        let need = Math.ceil(quest.target - quest.progress)
        while (need > 0) {
          const amount = Math.min(need, 5000)
          const added = await apiFetch('/v1/nutrition/water', { method: 'POST', body: JSON.stringify({ amount_ml: amount }) })
          if (added.status !== 201) return
          created.water.push(added.body.id)
          need -= amount
        }
      },
      daily_meals_3: async (quest) => {
        for (let index = quest.progress; index < quest.target; index += 1) {
          const extra = await apiFetch('/v1/nutrition/meals', {
            method: 'POST',
            body: JSON.stringify({ meal_type: 'snack', items: [{ name: 'Яблоко (ui-check)', grams: 150, kcal_per_100g: 47, protein_per_100g: 0.4, fat_per_100g: 0.4, carbs_per_100g: 10 }] }),
          })
          if (extra.status === 201) created.meals.push(extra.body.id)
        }
      },
      daily_steps: async (quest) => {
        const put = await apiFetch(`/v1/health/steps/${day}`, { method: 'PUT', body: JSON.stringify({ steps: Math.ceil(quest.target) }) })
        created.steps = put.status === 200
      },
      daily_review: async () => {
        const put = await apiFetch(`/v1/planner/journal/${day}`, { method: 'PUT', body: JSON.stringify({ note_md: 'Итоги дня (ui-check)', rating: 4 }) })
        created.journal = put.status === 200
      },
      daily_sleep: async () => {
        const put = await apiFetch(`/v1/health/sleep/${day}`, { method: 'PUT', body: JSON.stringify({ bed_time: '23:30', wake_time: '07:15' }) })
        created.sleep = put.status === 200
      },
      daily_p1: async () => {
        const extra = await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ title: 'P1 (ui-check)', priority: 1 }) })
        if (extra.status !== 201) return
        created.tasks.push(extra.body.id)
        await apiFetch(`/v1/planner/tasks/${extra.body.id}/complete`, { method: 'POST', body: '{}' })
      },
    }
    let snapshot = await game()
    let quest = snapshot.quests.find((item) => item.status === 'completed')
    if (!quest) {
      const easy = snapshot.quests.find((item) => item.status === 'active' && completeQuest[item.code])
      if (easy) {
        await completeQuest[easy.code](easy)
        snapshot = await game()
        quest = snapshot.quests.find((item) => item.id === easy.id && item.status === 'completed')
      }
    }
    if (!quest) {
      bad('a quest can be completed for the claim check', snapshot.quests.map((item) => `${item.code}:${item.status}`).join(', '))
    } else {
      const notDone = snapshot.quests.find((item) => item.status === 'active')
      if (notDone) {
        // from Node, not the page: an expected 409 must not show up as a browser console error
        const token = await page.evaluate(() => window.localStorage.getItem('dsos.device_token'))
        const early = await page.request.post(`${WEB_URL}/api/v1/gamification/quests/${notDone.id}/claim`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        const earlyBody = await early.json().catch(() => null)
        check(
          early.status() === 409 && earlyBody?.error?.code === 'quest_not_completed',
          'an unfinished quest cannot be claimed (409 quest_not_completed)',
          notDone.code,
        )
      }
      await openToday()
      const row = card.locator(`[data-testid="quest-row"][data-quest="${quest.code}"]`)
      await row.scrollIntoViewIfNeeded()
      const totalBefore = snapshot.xp.total
      const claimed = await after('POST', `/v1/gamification/quests/${quest.id}/claim`, () => row.locator('[data-testid="quest-claim"]').click())
      const claimBody = await claimed.json().catch(() => null)
      const toast = await page
        .locator('[role="status"]')
        .filter({ hasText: `+${quest.xp_reward} XP — «${quest.title_ru}»` })
        .waitFor({ timeout: 6000 })
        .then(() => true)
        .catch(() => false)
      check(
        claimed.status() === 200 && claimBody?.xp_awarded === quest.xp_reward && toast,
        `Claim → toast «+${quest.xp_reward} XP»`,
        quest.title_ru,
      )
      await shot('53-quest-claimed')
      const afterClaim = await game()
      const statusShown = await until(async () => (await row.getAttribute('data-status')) === 'claimed')
      check(
        afterClaim.xp.total >= totalBefore + quest.xp_reward &&
          afterClaim.quests.find((item) => item.id === quest.id)?.status === 'claimed' &&
          statusShown,
        'XP total grows by the reward, the quest shows «Получено»',
        `${totalBefore} → ${afterClaim.xp.total} XP`,
      )
      const again = await apiFetch(`/v1/gamification/quests/${quest.id}/claim`, { method: 'POST' })
      const totalAgain = (await game()).xp.total
      check(
        again.status === 200 && again.body?.xp_awarded === 0 && again.body?.quest?.status === 'claimed' && totalAgain === afterClaim.xp.total,
        'a second claim is a no-op (same quest, 0 XP, total unchanged)',
      )
    }

    // 6. /stats/achievements from the card: level, XP bars, streaks, quests, achievements
    await openToday()
    await card.locator('[data-testid="day-score-more"]').click()
    await page.waitForURL(/\/stats\/achievements$/, { timeout: 8000 })
    await page.locator('[data-testid="achievement-tile"]').first().waitFor({ timeout: 15_000 })
    const profile = (await apiFetch('/v1/gamification/profile')).body
    const levelCard = page.locator('[data-testid="level-card"]')
    const bars = await page.locator('[data-testid="xp-bars"] rect').evaluateAll((els) => els.map((el) => Number(el.getAttribute('data-xp'))))
    check(
      Number(await levelCard.getAttribute('data-level')) === profile.xp.level.level &&
        Number(await levelCard.getAttribute('data-total')) === profile.xp.total &&
        JSON.stringify(bars) === JSON.stringify(profile.xp.last_30_days.map((d) => d.xp)),
      'achievements screen: level card and 30 XP bars = GET /gamification/profile',
      `ур. ${profile.xp.level.level}, ${profile.xp.total} XP, today ${bars.at(-1)} XP`,
    )
    const streakRows = await page
      .locator('[data-testid="streak-row"]')
      .evaluateAll((els) => els.map((el) => `${el.dataset.kind}:${el.dataset.current}:${el.dataset.longest}:${el.dataset.freeze}`))
    check(
      JSON.stringify(streakRows) === JSON.stringify(profile.streaks.map((s) => `${s.kind}:${s.current}:${s.longest}:${s.freeze_tokens}`)) &&
        (await page.locator('[data-testid="streaks-card"]').innerText()).includes('без плана — 3'),
      'all 7 streaks with current, longest and freezes; the weekly workout rule explained',
      streakRows.slice(0, 3).join(' '),
    )
    const todayQuests = (await game()).quests
    check(
      (await page.locator('[data-testid="quests-card"] [data-testid="quest-row"]').count()) === todayQuests.length &&
        (await page.locator('[data-testid="quests-weekly"] [data-testid="quest-row"]').count()) ===
          todayQuests.filter((q) => q.kind === 'weekly').length,
      'daily and weekly quests listed',
      todayQuests.map((q) => q.code).join(', '),
    )
    const tiles = await page
      .locator('[data-testid="achievement-tile"]')
      .evaluateAll((els) => els.map((el) => ({ code: el.dataset.code, unlocked: el.dataset.unlocked === 'true', masked: el.dataset.masked === 'true', text: el.innerText })))
    const unlocked = profile.achievements.filter((a) => a.unlocked)
    const masked = profile.achievements.filter((a) => a.masked)
    const lockedWithProgress = await page.locator('[data-testid="achievement-tile"][data-unlocked="false"] [data-testid="achievement-progress"]').count()
    check(
      tiles.length === profile.achievements.length &&
        tiles.filter((t) => t.unlocked).length === unlocked.length &&
        unlocked.length >= 1 &&
        lockedWithProgress === profile.achievements.filter((a) => !a.unlocked && !a.masked).length &&
        lockedWithProgress >= 1,
      'grid: every achievement, the unlocked ones and the locked ones with progress',
      `${unlocked.length} unlocked of ${tiles.length}, ${lockedWithProgress} with progress`,
    )
    const unlockedFirst = await page
      .locator('[data-testid="achievement-category"]')
      .evaluateAll((sections) =>
        sections.every((section) => {
          const flags = [...section.querySelectorAll('[data-testid="achievement-tile"]')].map((el) => el.dataset.unlocked === 'true')
          return flags.every((flag, index) => index === 0 || !flag || flags[index - 1])
        }),
      )
    const maskedTiles = tiles.filter((t) => t.masked)
    check(
      maskedTiles.length === masked.length &&
        masked.length >= 1 &&
        maskedTiles.every((t) => t.code === 'secret' && t.text.includes('???')) &&
        unlockedFirst,
      'secret ones are «???» while locked; unlocked first in every category',
      `${masked.length} secret`,
    )
    await shot('54-achievements')
    await page.locator('[data-testid="achievement-tile"][data-masked="true"]').first().click()
    const detailSheet = page.locator('[data-testid="achievement-sheet"]')
    await detailSheet.waitFor({ timeout: 5000 })
    check((await detailSheet.innerText()).includes('Секретное — откроется, когда заработаете'), 'a secret tile opens without revealing it')
    await page.locator('[role="dialog"]').getByRole('button', { name: 'Close' }).click()
    await detailSheet.waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})
    await page.locator('[data-testid="achievements-grid"]').scrollIntoViewIfNeeded()
    await shot('55-achievements-grid')
    const rulesText = norm(await page.locator('[data-testid="xp-rules"]').innerText())
    check(
      (await page.locator('[data-testid="xp-rule"]').count()) === 5 &&
        rulesText.includes('тренировка +40') &&
        rulesText.includes('P1 +12') &&
        rulesText.includes('безопасного минимума'),
      '«Как начисляется опыт» lists the server rules (5 groups, the safe-floor note)',
    )
    await page.locator('[data-testid="achievements-to-today"]').click()
    await page.waitForURL(/\/today$/, { timeout: 8000 })
    ok('the screen links back to Today')
  } catch (error) {
    bad('stage 8 checks', String(error?.message ?? error))
  } finally {
    page.off('console', onConsole)
    page.off('pageerror', onPageError)
    await page.emulateMedia({ reducedMotion: null }).catch(() => {})
    check(errors.length === 0, 'no console errors during the stage-8 checks', errors.slice(0, 2).join(' | '))
    // clean up: meals, water, tasks, journal, steps and sleep this module logged
    const statuses = []
    for (const id of created.meals) statuses.push((await apiFetch(`/v1/nutrition/meals/${id}`, { method: 'DELETE' })).status)
    for (const id of created.water) statuses.push((await apiFetch(`/v1/nutrition/water/${id}`, { method: 'DELETE' })).status)
    for (const id of created.tasks) statuses.push((await apiFetch(`/v1/planner/tasks/${id}`, { method: 'DELETE' })).status)
    if (created.journal) statuses.push((await apiFetch(`/v1/planner/journal/${day}`, { method: 'DELETE' })).status)
    if (created.steps) statuses.push((await apiFetch(`/v1/health/steps/${day}`, { method: 'DELETE' })).status)
    if (created.sleep) statuses.push((await apiFetch(`/v1/health/sleep/${day}`, { method: 'DELETE' })).status)
    const dayAfter = await nutritionDay().catch(() => null)
    check(
      statuses.every((status) => status === 200 || status === 204) &&
        foodBefore != null &&
        Math.round(dayAfter?.totals?.kcal ?? -1) === Math.round(foodBefore.kcal) &&
        dayAfter?.water_ml === waterStart,
      'stage-8 test data removed (claimed-quest and achievement XP stay: permanent by design)',
      `${statuses.length} rows deleted`,
    )
  }
}
