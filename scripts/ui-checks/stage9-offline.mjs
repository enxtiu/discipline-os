/**
 * Browser checks — stage 9 offline sync polish and conflict surfacing.
 * Called by scripts/ui-check.mjs with shared helpers; owned by the offline workstream.
 *
 * What is proven, always against the server's state (a fresh read), not only the screen:
 *   - meals and water logged offline show at once (marked «ждёт отправки», totals moved)
 *     and are sent on reconnect with the server's totals equal to what was shown;
 *     a meal deleted before it was sent is withdrawn from the queue;
 *   - a task edited offline shows the edit before it is sent;
 *   - a conflicting edit from a second browser (same account) lands in the sync sheet as
 *     a conflict, and both «Сохранить мою» and «Оставить их версию» work; an online
 *     editor conflict and a journal conflict are settled inline; an edit of a task deleted
 *     elsewhere says so;
 *   - a write the server refuses (422) is kept as failed with its reason, survives a reload
 *     and can be removed;
 *   - a 5xx with Retry-After stops the replay and backs off, and a subtask never overtakes
 *     its parent;
 *   - the header pill counts what waits and dots what needs attention; the sync sheet works
 *     offline, has 48 px targets and respects reduced motion.
 * Everything created is removed at the end, and the last check proves it.
 */
export async function run(ctx) {
  const { page, context, WEB_URL, check, ok, shot, setOffline, allowErrors } = ctx
  console.log('\n7¹³. stage 9 offline sync & conflicts')

  const P = '/v1/planner'
  const apiOn = (target) => (path, init = {}) =>
    target.evaluate(
      async ({ path, init }) => {
        const token = window.localStorage.getItem('dsos.device_token')
        const response = await fetch(`/api${path}`, {
          ...init,
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
        })
        return { status: response.status, body: await response.json().catch(() => null) }
      },
      { path, init },
    )
  const api = apiOn(page)
  const send = (method, path, body) => api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) })
  /** The server as it is, read from here (not through the page, which may be offline). */
  const token = await page.evaluate(() => window.localStorage.getItem('dsos.device_token'))
  const server = async (path, init = {}) => {
    const response = await fetch(`${WEB_URL}/api${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
    })
    return { status: response.status, body: await response.json().catch(() => null) }
  }
  const sleep = (ms) => page.waitForTimeout(ms)
  /** Re-read until `done(value)` (the server may answer a moment after the UI). */
  const until = async (read, done, timeout = 10_000) => {
    const end = Date.now() + timeout
    let value = await read()
    while (!done(value) && Date.now() < end) {
      await sleep(250)
      value = await read()
    }
    return value
  }
  /** The offline queue as stored (IndexedDB `dsos` → `queue`), read like the app reads it. */
  const queueOn = (target) => () =>
    target.evaluate(
      () =>
        new Promise((resolve) => {
          const open = indexedDB.open('dsos')
          open.onerror = () => resolve([])
          open.onsuccess = () => {
            const db = open.result
            if (!db.objectStoreNames.contains('queue')) {
              db.close()
              resolve([])
              return
            }
            const request = db.transaction('queue', 'readonly').objectStore('queue').getAll()
            request.onsuccess = () => {
              db.close()
              resolve(request.result)
            }
            request.onerror = () => {
              db.close()
              resolve([])
            }
          }
        }),
    )
  const queue = queueOn(page)
  const statusOf = (entry) => entry.status ?? 'pending'
  const pill = page.locator('[data-testid="sync-pill"]')
  const pillState = async () => ({
    pending: Number(await pill.getAttribute('data-pending')),
    attention: Number(await pill.getAttribute('data-attention')),
    online: (await pill.getAttribute('data-online')) === 'true',
  })
  const sheet = page.locator('[data-testid="sync-sheet"]')
  const openSheet = async (target = page) => {
    await target.locator('[data-testid="sync-pill"]').click()
    await target.locator('[data-testid="sync-sheet"]').waitFor({ timeout: 8000 })
    await target.waitForTimeout(400)
  }
  const closeDialog = async (target = page) => {
    await target.locator('[role="dialog"]').last().getByRole('button', { name: /^(Закрыть|Close)$/ }).first().click()
    await target.waitForTimeout(500)
  }
  const goOnline = async (target = page) => {
    await setOffline(false)
    await target.evaluate(() => window.dispatchEvent(new Event('online')))
  }
  /**
   * Back online inside `allowErrors` (the replay answers 4xx/5xx on purpose): the context
   * goes online without ending the error allowance, which also covers the late console
   * lines Chromium prints for those answers.
   */
  const expectRefusals = async (until_) =>
    allowErrors(async () => {
      await context.setOffline(false)
      await page.evaluate(() => window.dispatchEvent(new Event('online')))
      const result = await until_()
      await sleep(700)
      return result
    })
  /** Client-side navigation through the tab bar (a reload would need the network). */
  const tab = async (href) => {
    await page.locator(`nav.tabbar a[href="${href}"]`).click()
    await sleep(700)
  }
  const go = async (route, target = page) => {
    await target.goto(`${WEB_URL}${route}`, { waitUntil: 'networkidle' })
    await target.waitForTimeout(400)
  }
  const toasts = page.locator('[data-testid="plan-toast"]')
  const clearToasts = async () => {
    for (const toast of await toasts.all()) await toast.locator('p').click().catch(() => {})
    await toasts.first().waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
  }
  const row = (target, id) => target.locator(`[data-testid="task-row"][data-task-id="${id}"]`)
  const openTask = async (target, id) => {
    await row(target, id).first().locator('[data-testid="task-title"]').click()
    await target.locator(`[data-testid="task-sheet"][data-task-id="${id}"]`).waitFor({ timeout: 8000 })
    await target.waitForTimeout(300)
  }
  const getTask = async (id) => (await server(`${P}/tasks/${id}`)).body
  const day = async () => (await server('/v1/nutrition/day')).body
  const box = async (locator) => {
    const rect = await locator.boundingBox()
    return rect ? `${Math.round(rect.width)}×${Math.round(rect.height)}` : 'missing'
  }
  const tall = async (locator) => {
    const rect = await locator.boundingBox()
    return !!rect && rect.height >= 44 && rect.width >= 44
  }

  const stamp = String(Date.now()).slice(-5)
  const made = { tasks: new Set(), meals: new Set(), water: new Set() }
  const today = (await api(`${P}/counts`)).body?.date
  const journalBefore = today ? (await api(`${P}/journal/${today}`)).body?.entry ?? null : null
  let second = null
  let legacy = null

  try {
    // ---- 0. the pill and the sheet, everything sent --------------------------------
    await go('/today')
    const idle = await until(pillState, (state) => state.pending === 0)
    check(idle.pending === 0 && idle.attention === 0 && idle.online, 'pill: online, nothing waiting, no attention dot')
    check(await tall(pill), 'the pill is a 48 px tap target', await box(pill))
    await openSheet()
    check(
      (await page.locator('[data-testid="sync-empty"]').isVisible()) &&
        (await sheet.innerText()).includes('Всё отправлено'),
      'sync sheet: «Всё отправлено» when nothing waits',
    )
    await closeDialog()

    // a recent food to log from (the search needs the network; recents are already on the phone)
    const found = (await api(`/v1/foods/search?q=${encodeURIComponent('гречка')}`)).body?.items?.[0]
    const seedMeal = crypto.randomUUID()
    if (found) {
      await send('POST', '/v1/nutrition/meals', { id: seedMeal, meal_type: 'snack', items: [{ food_id: found.id, grams: 100 }] })
      made.meals.add(seedMeal)
    }
    await go('/food')
    await go('/today')
    await go('/food')
    const recentChip = page.locator('section').filter({ hasText: 'Недавние' }).locator('button.chip').first()
    await recentChip.waitFor({ timeout: 8000 }).catch(() => {})
    const foodName = (await recentChip.innerText().catch(() => '')).trim()

    // ---- 1. a meal and water logged offline -------------------------------------------
    const before = await day()
    await setOffline(true)
    await recentChip.click()
    await page.getByText('Записать в дневник').waitFor({ timeout: 8000 })
    const previewKcal = Number(
      ((await page.locator('[role="dialog"] .surface-sunken p.stat-value').first().innerText()) || '0').replace(/\s/g, '').replace(',', '.'),
    )
    await page.getByRole('button', { name: 'Записать в дневник' }).click()
    const pendingMeal = page.locator('[data-testid="meal-row"][data-pending="true"]')
    await pendingMeal.first().waitFor({ timeout: 8000 }).catch(() => {})
    await page.getByText('запишется, когда появится сеть').first().waitFor({ timeout: 5000 }).catch(() => {})
    const offlineMealId = await pendingMeal.first().getAttribute('data-meal-id').catch(() => null)
    if (offlineMealId) made.meals.add(offlineMealId)
    check(
      (await pendingMeal.count()) === 1 &&
        (await pendingMeal.first().innerText()).includes('ждёт отправки') &&
        (await page.locator('body').innerText()).includes('запишется, когда появится сеть'),
      'offline: the logged meal shows at once on Food, marked «ждёт отправки»',
      foodName,
    )
    await tab('/today')
    const todayPending = page.locator('[data-testid="meal-row"][data-pending="true"]')
    await todayPending.first().waitFor({ timeout: 5000 }).catch(() => {})
    const eatenText = async () =>
      Number((await page.locator('div.surface-sunken').filter({ hasText: 'Съедено' }).locator('p.stat-value').innerText()).replace(/[^\d]/g, ''))
    const eatenShown = await eatenText().catch(() => NaN)
    check(
      (await todayPending.count()) === 1 && Math.abs(eatenShown - Math.round(before.totals.kcal + previewKcal)) <= 1,
      'offline: Today shows the meal in its slot and the eaten total moves by its kcal',
      `${Math.round(before.totals.kcal)} + ${previewKcal} → ${eatenShown}`,
    )
    const waterLine = page.locator('span.tnum.muted').filter({ hasText: 'мл' }).first()
    const waterBefore = await waterLine.innerText()
    await page.getByRole('button', { name: /\+250 мл/ }).click()
    const waterAfter = await until(() => waterLine.innerText(), (value) => value !== waterBefore, 8000)
    await page.locator('[data-testid="water-pending"]').waitFor({ timeout: 5000 }).catch(() => {})
    check(
      waterBefore !== waterAfter && (await page.locator('[data-testid="water-pending"]').isVisible()),
      'offline: +250 ml moves the water total at once, marked «ждёт отправки»',
      `${waterBefore} → ${waterAfter}`,
    )
    const offlinePill = await pillState()
    check(
      !offlinePill.online && offlinePill.pending === 2 && (await page.locator('[data-testid="sync-pill-count"]').innerText()) === '2',
      'pill: offline, counts the two waiting writes',
      JSON.stringify(offlinePill),
    )
    await openSheet()
    const offlineSheet = await sheet.innerText()
    const groups = await page.locator('[data-testid="sync-group"]').evaluateAll((els) => els.map((el) => el.dataset.kind))
    check(
      (await page.locator('[data-testid="sync-status-line"]').getAttribute('data-online')) === 'false' &&
        offlineSheet.includes('Нет сети') &&
        groups.includes('meal') &&
        groups.includes('water') &&
        offlineSheet.includes('Приём пищи') &&
        offlineSheet.includes('Вода') &&
        /только что|мин назад/.test(offlineSheet),
      'sync sheet offline: grouped by kind with human labels and when they were made',
      groups.join(', '),
    )
    await shot('9o-sync-sheet-offline')
    await closeDialog()
    await shot('9o-today-offline-pending')

    // a meal logged offline and deleted before it was sent never reaches the server
    await tab('/food')
    await recentChip.click()
    await page.getByText('Записать в дневник').waitFor({ timeout: 8000 })
    await page.getByRole('button', { name: 'Записать в дневник' }).click()
    const twoPending = page.locator('[data-testid="meal-row"][data-pending="true"]')
    await until(() => twoPending.count(), (count) => count === 2, 8000)
    const withdrawnId = await twoPending.nth(1).getAttribute('data-meal-id').catch(() => null)
    const queuedBeforeWithdraw = (await queue()).length
    await twoPending.nth(1).locator('[data-testid="meal-delete"]').click()
    await until(() => twoPending.count(), (count) => count === 1, 8000)
    const queuedAfterWithdraw = await until(queue, (entries) => entries.length === queuedBeforeWithdraw - 1, 5000)
    check(
      (await twoPending.count()) === 1 &&
        queuedAfterWithdraw.length === queuedBeforeWithdraw - 1 &&
        !queuedAfterWithdraw.some((entry) => entry.body?.id === withdrawnId),
      'offline: deleting a meal that was not sent yet withdraws it from the queue',
    )

    // back online: both writes go, the server's totals equal what was shown
    await goOnline()
    const drained = await until(queue, (entries) => entries.length === 0, 15_000)
    const after = await until(day, (value) => (value?.meals ?? []).some((meal) => meal.client_uuid === offlineMealId))
    const serverMeal = (after?.meals ?? []).find((meal) => meal.client_uuid === offlineMealId)
    check(
      drained.length === 0 &&
        !!serverMeal &&
        Math.abs(after.totals.kcal - before.totals.kcal - serverMeal.kcal) < 0.01 &&
        Math.abs(serverMeal.kcal - previewKcal) <= 1 &&
        after.water_ml === before.water_ml + 250 &&
        !(after.meals ?? []).some((meal) => meal.client_uuid === withdrawnId),
      'online: the queued meal and water reach the server once; its totals equal what was shown',
      `kcal ${before.totals.kcal} → ${after?.totals?.kcal}, water ${before.water_ml} → ${after?.water_ml}`,
    )
    for (const row of after?.water_logs ?? []) if (!(before.water_logs ?? []).some((old) => old.id === row.id)) made.water.add(row.id)
    await tab('/today')
    await until(
      async () => page.locator('[data-testid="meal-row"][data-pending="true"]').count(),
      (count) => count === 0,
    )
    const settledRow = page.locator(`[data-testid="meal-row"][data-meal-id="${offlineMealId}"]`)
    check(
      (await settledRow.count()) === 1 &&
        (await settledRow.getAttribute('data-pending')) === 'false' &&
        (await page.locator('[data-testid="water-pending"]').count()) === 0 &&
        Math.abs((await eatenText()) - Math.round(after.totals.kcal)) <= 1,
      'the server\'s answer replaces the optimistic rows (no «ждёт отправки», same totals)',
    )
    // delete that meal offline: it leaves at once and the delete is sent later
    await setOffline(true)
    await settledRow.locator('[data-testid="meal-delete"]').click()
    const goneAtOnce = (await until(() => settledRow.count(), (count) => count === 0, 8000)) === 0
    await goOnline()
    const afterDelete = await until(day, (value) => !(value?.meals ?? []).some((meal) => meal.client_uuid === offlineMealId))
    check(
      goneAtOnce && !(afterDelete?.meals ?? []).some((meal) => meal.client_uuid === offlineMealId),
      'a meal deleted offline disappears at once; the delete reaches the server on reconnect',
    )
    made.meals.delete(offlineMealId)

    // ---- 2. a task edited offline shows the edit at once ----------------------------------
    const create = async (body) => {
      const result = (await send('POST', `${P}/tasks`, { id: crypto.randomUUID(), ...body })).body
      if (result?.id) made.tasks.add(result.id)
      return result
    }
    const edited = await create({ title: `UI офлайн-правка ${stamp}`, due_date: today })
    const ticked = await create({ title: `UI офлайн-галочка ${stamp}`, due_date: today })
    await go('/plan')
    await openTask(page, edited.id) // the editor's chunk is loaded while online
    await closeDialog()
    await setOffline(true)
    await openTask(page, edited.id)
    await page.locator('[data-testid="task-title-input"]').fill(`UI офлайн-правка ${stamp} (с телефона)`)
    await page.locator('[data-testid="task-save"]').click()
    const editedRow = row(page, edited.id).first()
    await until(async () => (await editedRow.innerText().catch(() => '')).includes('(с телефона)'), Boolean, 8000)
    await until(() => editedRow.getAttribute('data-pending'), (value) => value === 'true', 5000)
    check(
      (await editedRow.innerText()).includes('(с телефона)') &&
        (await editedRow.getAttribute('data-pending')) === 'true' &&
        (await editedRow.innerText()).includes('ждёт отправки') &&
        (await getTask(edited.id))?.title === `UI офлайн-правка ${stamp}`,
      'offline: an edited task shows its new title at once, marked «ждёт отправки» (the server still has the old one)',
    )
    const queuedEdit = (await queue()).find((entry) => entry.path === `${P}/tasks/${edited.id}`)
    check(
      queuedEdit?.method === 'PATCH' && queuedEdit?.body?.base?.title === `UI офлайн-правка ${stamp}`,
      'the queued edit carries base (the title it started from)',
    )
    // ticking another one offline: done at once
    await row(page, ticked.id).first().locator('[data-testid="task-check"]').click()
    await until(() => row(page, ticked.id).first().getAttribute('data-status'), (value) => value === 'done', 5000)
    check((await row(page, ticked.id).first().getAttribute('data-status')) === 'done', 'offline: completing a task shows at once')
    await shot('9o-plan-offline-edit')
    await goOnline()
    const sentEdit = await until(() => getTask(edited.id), (task) => task?.title?.endsWith('(с телефона)'))
    const sentTick = await until(() => getTask(ticked.id), (task) => task?.status === 'done')
    check(
      sentEdit?.title === `UI офлайн-правка ${stamp} (с телефона)` && sentTick?.status === 'done',
      'online: the edit and the completion reach the server',
    )
    await until(async () => row(page, edited.id).first().getAttribute('data-pending'), (value) => value === 'false')
    check(
      (await row(page, edited.id).first().getAttribute('data-pending')) === 'false' &&
        (await row(page, edited.id).first().innerText()).includes('(с телефона)'),
      'once sent, the row shows the server\'s copy without «ждёт отправки»',
    )

    // ---- 3. a conflict from a second browser, settled in the sync sheet ------------------
    second = await context.browser().newContext({ viewport: { width: 390, height: 844 }, locale: 'ru-RU', timezoneId: 'Europe/Moscow' })
    await second.addInitScript((value) => {
      if (!window.localStorage.getItem('dsos.device_token')) window.localStorage.setItem('dsos.device_token', value)
    }, token)
    const other = await second.newPage()
    const otherErrors = []
    other.on('console', (message) => {
      if (message.type() === 'error' && !/favicon|404 \(Not Found\)/i.test(message.text())) otherErrors.push(message.text())
    })
    other.on('pageerror', (error) => otherErrors.push(String(error)))
    const editOn = async (target, id, title) => {
      await openTask(target, id)
      await target.locator('[data-testid="task-title-input"]').fill(title)
      await target.locator('[data-testid="task-save"]').click()
      await target.locator(`[data-testid="task-sheet"][data-task-id="${id}"]`).waitFor({ state: 'detached', timeout: 8000 }).catch(() => {})
    }
    const contested = await create({ title: `UI конфликт ${stamp}`, due_date: today })
    await go('/plan')
    await go('/plan', other)
    await openTask(page, contested.id)
    await closeDialog()
    // this phone, offline: a new title waits in the queue …
    await setOffline(true)
    await editOn(page, contested.id, `UI конфликт ${stamp} — телефон`)
    // … while the other device renames the same task
    await editOn(other, contested.id, `UI конфликт ${stamp} — ноутбук`)
    const otherTitle = (await getTask(contested.id))?.title
    check(otherTitle === `UI конфликт ${stamp} — ноутбук`, 'second browser: renamed the task (UI) while the phone is offline')
    await expectRefusals(() => until(queue, (entries) => entries.some((entry) => statusOf(entry) === 'conflict'), 15_000))
    const conflictEntry = (await queue()).find((entry) => statusOf(entry) === 'conflict')
    const attention = await until(pillState, (state) => state.attention === 1)
    check(
      conflictEntry?.conflict?.fields?.includes('title') &&
        conflictEntry?.conflict?.current?.title === otherTitle &&
        attention.attention === 1 &&
        (await page.locator('[data-testid="sync-pill-dot"]').isVisible()),
      'replay: the queued edit meets 409 edit_conflict → kept as a conflict, the pill shows the dot',
      otherTitle,
    )
    await openSheet()
    const conflictRow = page.locator('[data-testid="sync-entry"][data-status="conflict"]')
    check(
      (await conflictRow.count()) === 1 && (await conflictRow.innerText()).includes('Изменено на другом устройстве'),
      'sync sheet: the conflict is listed with «Открыть»',
    )
    await conflictRow.locator('[data-testid="sync-open"]').click()
    await page.locator('[data-testid="sync-conflict"] [data-testid="conflict-view"]').waitFor({ timeout: 5000 })
    const theirsText = await page.locator('[data-testid="sync-conflict"] [data-testid="conflict-theirs"]').first().innerText()
    const mineText = await page.locator('[data-testid="sync-conflict"] [data-testid="conflict-mine"]').first().innerText()
    check(
      theirsText.includes('ноутбук') && mineText.includes('телефон'),
      '«Открыть» shows both values: theirs and mine',
      `${theirsText} / ${mineText}`,
    )
    check(
      (await tall(page.locator('[data-testid="conflict-keep-mine"]'))) && (await tall(page.locator('[data-testid="conflict-keep-theirs"]'))),
      'the conflict buttons are 48 px targets',
    )
    await shot('9o-sync-conflict')
    await page.locator('[data-testid="conflict-keep-mine"]').click()
    const mine = await until(() => getTask(contested.id), (task) => task?.title?.endsWith('телефон'))
    const mineQueue = await until(queue, (entries) => entries.length === 0)
    check(
      mine?.title === `UI конфликт ${stamp} — телефон` && mineQueue.length === 0,
      '«Сохранить мою» sends the edit again without the check: the server has this phone\'s title',
    )
    await closeDialog()

    // the other way: keep theirs
    await go('/plan')
    await openTask(page, contested.id)
    await closeDialog()
    await setOffline(true)
    await openTask(page, contested.id)
    await page.locator('[data-testid="priority-1"]').click()
    await page.locator('[data-testid="task-title-input"]').fill(`UI конфликт ${stamp} — снова телефон`)
    await page.locator('[data-testid="task-save"]').click()
    await sleep(600)
    await go('/plan', other)
    await editOn(other, contested.id, `UI конфликт ${stamp} — снова ноутбук`)
    await expectRefusals(() => until(queue, (entries) => entries.some((entry) => statusOf(entry) === 'conflict'), 15_000))
    await openSheet()
    await page.locator('[data-testid="sync-open"]').first().click()
    await page.locator('[data-testid="conflict-keep-theirs"]').waitFor({ timeout: 5000 })
    await page.locator('[data-testid="conflict-keep-theirs"]').click()
    const theirs = await until(() => getTask(contested.id), (task) => task?.priority === 1)
    check(
      theirs?.title === `UI конфликт ${stamp} — снова ноутбук` && theirs?.priority === 1 && (await queue()).length === 0,
      '«Оставить их версию» keeps the other title and still sends the rest of the edit (priority)',
      `${theirs?.title} · P${theirs?.priority}`,
    )
    await closeDialog()
    await until(async () => (await row(page, contested.id).first().innerText().catch(() => '')).includes('снова ноутбук'), Boolean)
    check(
      (await row(page, contested.id).first().innerText()).includes('снова ноутбук') && (await pillState()).attention === 0,
      'the phone shows the other title afterwards; no attention dot',
    )

    // ---- 3b. online editor conflict, settled inline ---------------------------------------
    await openTask(page, contested.id)
    await editOn(other, contested.id, `UI конфликт ${stamp} — ноутбук, онлайн`)
    await page.locator('[data-testid="task-title-input"]').fill(`UI конфликт ${stamp} — телефон, онлайн`)
    await allowErrors(async () => {
      await page.locator('[data-testid="task-save"]').click()
      await page.locator('[data-testid="task-sheet"] [data-testid="conflict-view"]').waitFor({ timeout: 8000 })
      await sleep(700)
    })
    const inline = page.locator('[data-testid="task-sheet"] [data-testid="conflict-view"]')
    check(
      (await inline.innerText()).includes('Изменено на другом устройстве') &&
        (await inline.locator('[data-testid="conflict-theirs"]').first().innerText()).includes('ноутбук, онлайн'),
      'editor online: 409 → inline «Изменено на другом устройстве» with the other value',
    )
    await shot('9o-editor-conflict')
    await inline.locator('[data-testid="conflict-keep-theirs"]').click()
    await until(() => page.locator('[data-testid="task-title-input"]').inputValue(), (value) => value.includes('ноутбук, онлайн'), 8000)
    check(
      (await page.locator('[data-testid="task-title-input"]').inputValue()).includes('ноутбук, онлайн') &&
        (await getTask(contested.id))?.title === `UI конфликт ${stamp} — ноутбук, онлайн`,
      '«Оставить их версию» in the editor shows their title and writes nothing',
    )
    await closeDialog()

    // ---- 3c. journal conflict, online, settled inline («Сохранить мою») -------------------
    await go('/plan/journal')
    await page.locator('[data-testid="journal-wins"]').fill(`Победа телефона ${stamp}`)
    await until(async () => page.locator('[data-testid="journal-status"]').getAttribute('data-status'), (value) => value === 'saved')
    await apiOn(other)(`${P}/journal/${today}`, { method: 'PUT', body: JSON.stringify({ wins_md: `Победа ноутбука ${stamp}` }) })
    await allowErrors(async () => {
      await page.locator('[data-testid="journal-wins"]').fill(`Победа телефона ${stamp}, дополнено`)
      await page.locator('[data-testid="journal-page"] [data-testid="conflict-view"]').waitFor({ timeout: 10_000 })
      await sleep(700)
    })
    const journalConflict = page.locator('[data-testid="journal-page"] [data-testid="conflict-view"]')
    check(
      (await page.locator('[data-testid="journal-status"]').getAttribute('data-status')) === 'conflict' &&
        (await journalConflict.locator('[data-testid="conflict-theirs"]').first().innerText()).includes('ноутбука'),
      'journal: an autosave over a newer text from another device → inline conflict, nothing overwritten',
    )
    check(
      ((await server(`${P}/journal/${today}`)).body?.entry?.wins_md ?? '').includes('ноутбука'),
      'journal: the other device\'s text is still on the server while the choice is open',
    )
    await shot('9o-journal-conflict')
    await journalConflict.locator('[data-testid="conflict-keep-mine"]').click()
    const journalMine = await until(
      async () => (await server(`${P}/journal/${today}`)).body?.entry?.wins_md ?? '',
      (value) => value.includes('дополнено'),
    )
    check(journalMine === `Победа телефона ${stamp}, дополнено`, '«Сохранить мою» writes this phone\'s text')

    // ---- 3d. an edit of a task deleted elsewhere ------------------------------------------
    const doomed = await create({ title: `UI удалят ${stamp}`, due_date: today })
    await go('/plan')
    await openTask(page, doomed.id)
    await closeDialog()
    await setOffline(true)
    await editOn(page, doomed.id, `UI удалят ${stamp} — правка`)
    await apiOn(other)(`${P}/tasks/${doomed.id}`, { method: 'DELETE' })
    made.tasks.delete(doomed.id)
    await expectRefusals(() => until(queue, (entries) => entries.some((entry) => statusOf(entry) === 'failed'), 15_000))
    await openSheet()
    const goneRow = page.locator('[data-testid="sync-entry"][data-status="failed"]').first()
    check(
      (await goneRow.locator('[data-testid="sync-reason"]').innerText()).includes('Удалено на другом устройстве — правка не применена'),
      'an edit of a task deleted on another device: «Удалено на другом устройстве — правка не применена»',
    )
    await goneRow.locator('[data-testid="sync-discard"]').click()
    await until(queue, (entries) => entries.length === 0)
    await closeDialog()

    // ---- 4. a write the server refuses (422): kept as failed, survives a reload, removable ---
    const tomorrow = new Date(Date.parse(`${today}T12:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
    const refused = await create({ title: `UI отказ ${stamp}`, due_date: tomorrow })
    await go('/plan/upcoming')
    await openTask(page, refused.id)
    await closeDialog()
    await setOffline(true)
    await openTask(page, refused.id)
    await page.locator('[data-testid="reminder-15"]').click() // «за 15 мин» — needs the due date
    await page.locator('[data-testid="task-save"]').click()
    await sleep(600)
    // meanwhile the other device takes the date away: «за 15 мин» has nothing to count from
    await apiOn(other)(`${P}/tasks/${refused.id}`, { method: 'PATCH', body: JSON.stringify({ due_date: null }) })
    await expectRefusals(() => until(queue, (entries) => entries.some((entry) => statusOf(entry) === 'failed'), 15_000))
    const failedEntry = (await queue()).find((entry) => statusOf(entry) === 'failed')
    check(
      failedEntry?.error?.status === 422 && failedEntry?.error?.code === 'invalid_reminder',
      'replay: the server refuses the reminder (422 invalid_reminder) → the write is kept as failed',
      `${failedEntry?.error?.status} ${failedEntry?.error?.code}`,
    )
    await page.reload({ waitUntil: 'networkidle' })
    await sleep(800)
    const afterReload = await pillState()
    check(
      afterReload.attention === 1 && (await queue()).some((entry) => statusOf(entry) === 'failed'),
      'the failed write survives a reload (not lost); the pill keeps its dot',
      JSON.stringify(afterReload),
    )
    await openSheet()
    const failedRow = page.locator('[data-testid="sync-entry"][data-status="failed"]')
    const reason = await failedRow.locator('[data-testid="sync-reason"]').innerText()
    check(
      reason.includes('Сервер не принял данные') && (await failedRow.innerText()).includes('Задача'),
      'sync sheet: the failed write with the server\'s reason in plain words',
      reason,
    )
    check(
      (await tall(failedRow.locator('[data-testid="sync-retry"]'))) && (await tall(failedRow.locator('[data-testid="sync-discard"]'))),
      '«Повторить» and «Удалить» are 48 px targets',
    )
    await shot('9o-sync-failed')
    await allowErrors(async () => {
      await failedRow.locator('[data-testid="sync-retry"]').click()
      await until(queue, (entries) => entries.some((entry) => statusOf(entry) === 'failed' && entry.attempts >= 2))
      await sleep(700)
    })
    check((await queue()).some((entry) => statusOf(entry) === 'failed' && entry.attempts >= 2), '«Повторить» sends it again (refused again: still failed)')
    await failedRow.locator('[data-testid="sync-discard"]').click()
    await until(queue, (entries) => entries.length === 0)
    check((await queue()).length === 0 && (await pillState()).attention === 0, '«Удалить» removes it; the dot goes')
    await closeDialog()

    // ---- 5. 5xx + Retry-After: the replay backs off; a subtask never overtakes its parent ---
    await go('/plan/calendar?view=month')
    await page.locator('[data-testid="cal-add-on-day"]').click()
    await page.locator('[data-testid="task-title-input"]').waitFor({ timeout: 8000 })
    await closeDialog() // the editor chunk is loaded while online
    await setOffline(true)
    await page.locator('[data-testid="cal-add-on-day"]').click()
    await page.locator('[data-testid="task-title-input"]').fill(`UI родитель ${stamp}`)
    await page.locator('[data-testid="subtask-input"]').fill(`UI подзадача ${stamp}`)
    await page.locator('[data-testid="subtask-add"]').click()
    await page.locator('[data-testid="task-save"]').click()
    await sleep(900)
    const queuedTree = (await queue()).filter((entry) => entry.method === 'POST' && entry.path === `${P}/tasks`)
    const parentId = queuedTree.find((entry) => !entry.body?.parent_id)?.body?.id
    const childId = queuedTree.find((entry) => entry.body?.parent_id)?.body?.id
    if (parentId) made.tasks.add(parentId)
    if (childId) made.tasks.add(childId)
    const pillTree = await pillState()
    check(queuedTree.length === 2 && !!parentId && !!childId && pillTree.pending === 2, 'offline: a task with a subtask waits as two writes', JSON.stringify(pillTree))
    const seen = []
    let refusals = 0
    const failParent = async (route) => {
      const body = route.request().postDataJSON()
      seen.push({ id: body?.id, at: Date.now() })
      if (body?.id === parentId && refusals < 2) {
        refusals += 1
        await route.fulfill({
          status: 503,
          headers: { 'Content-Type': 'application/json', 'Retry-After': '2' },
          body: JSON.stringify({ error: { code: 'service_unavailable', message: 'maintenance' } }),
        })
        return
      }
      await route.continue()
    }
    await page.route('**/api/v1/planner/tasks', failParent)
    let retryLine = ''
    await expectRefusals(async () => {
      await until(async () => refusals, (value) => value >= 1, 8000)
      await openSheet()
      retryLine = await page.locator('[data-testid="sync-status-line"]').innerText().catch(() => '')
      return until(queue, (entries) => entries.length === 0, 30_000)
    })
    await page.unroute('**/api/v1/planner/tasks', failParent)
    await closeDialog().catch(() => {})
    const order = seen.map((item) => (item.id === parentId ? 'P' : item.id === childId ? 'C' : '?')).join('')
    const firstChild = seen.findIndex((item) => item.id === childId)
    const lastParent = seen.map((item) => item.id).lastIndexOf(parentId)
    const gap = seen.length >= 2 ? seen[1].at - seen[0].at : 0
    check(
      order === 'PPPC' && firstChild > lastParent,
      '503 stops the replay; the subtask is sent only after its parent got through',
      order,
    )
    check(gap >= 1800, 'the retry honours Retry-After (≥ 2 s between attempts)', `${gap} ms`)
    check(/следующая попытка через|Отправка/.test(retryLine), 'sync sheet: the status line says when the next try is', retryLine)
    const child = await until(() => getTask(childId), (task) => !!task?.id)
    check(child?.parent_id === parentId, 'both reached the server: the subtask under its parent')

    // ---- 6. reduced motion: the sheet appears without the slide ---------------------------
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await pill.click()
    const dialog = page.locator('[role="dialog"]').last()
    await dialog.waitFor({ timeout: 5000 })
    const y1 = (await dialog.boundingBox())?.y ?? -1
    await sleep(60)
    const y2 = (await dialog.boundingBox())?.y ?? -2
    check(Math.abs(y1 - y2) < 1, 'reduced motion: the sync sheet does not slide in', `${y1} → ${y2}`)
    await closeDialog()
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    check(otherErrors.length === 0, 'second browser: no console errors', otherErrors.slice(0, 2).join(' | '))

    // ---- 7. a write queued by a stage-1 build (Dexie v1, no status) is migrated and sent ---
    legacy = await context.browser().newContext({ viewport: { width: 390, height: 844 }, locale: 'ru-RU', timezoneId: 'Europe/Moscow' })
    await legacy.addInitScript((value) => {
      if (!window.localStorage.getItem('dsos.device_token')) window.localStorage.setItem('dsos.device_token', value)
    }, token)
    const old = await legacy.newPage()
    await old.goto(`${WEB_URL}/icons/favicon.svg`) // the origin, without the app: the stage-1 database is laid down first
    const legacyId = crypto.randomUUID()
    made.tasks.add(legacyId)
    const seeded = await old.evaluate(
      ({ id, title }) =>
        new Promise((resolve) => {
          const open = indexedDB.open('dsos', 10) // Dexie version 1 = IndexedDB version 10
          open.onupgradeneeded = () => {
            const store = open.result.createObjectStore('queue', { keyPath: 'id', autoIncrement: true })
            for (const index of ['clientUuid', 'createdAt', 'kind']) store.createIndex(index, index)
          }
          open.onerror = () => resolve(false)
          open.onsuccess = () => {
            const db = open.result
            const tx = db.transaction('queue', 'readwrite')
            tx.objectStore('queue').add({
              clientUuid: id,
              method: 'POST',
              path: '/v1/planner/tasks',
              body: { id, title },
              createdAt: Date.now() - 60_000,
              attempts: 0,
              kind: 'planner',
            })
            tx.oncomplete = () => {
              db.close()
              resolve(true)
            }
            tx.onerror = () => resolve(false)
          }
        }),
      { id: legacyId, title: `UI старая очередь ${stamp}` },
    )
    await old.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    const migrated = await until(() => getTask(legacyId), (task) => !!task?.id, 15_000)
    const leftOld = await until(queueOn(old), (entries) => entries.length === 0)
    check(
      seeded && migrated?.title === `UI старая очередь ${stamp}` && leftOld.length === 0,
      'a write queued by the stage-1 build (Dexie v1) is migrated to v2 and replayed once',
    )
  } finally {
    // ---- cleanup -----------------------------------------------------------------------
    await setOffline(false)
    await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {})
    await page.emulateMedia({ reducedMotion: 'no-preference' }).catch(() => {})
    if (second) await second.close().catch(() => {})
    if (legacy) await legacy.close().catch(() => {})
    // whatever is still queued on this phone (a failed step) is dropped, so later runs start clean
    await page
      .evaluate(
        () =>
          new Promise((resolve) => {
            const open = indexedDB.open('dsos')
            open.onsuccess = () => {
              const db = open.result
              if (!db.objectStoreNames.contains('queue')) return resolve(null)
              const tx = db.transaction('queue', 'readwrite')
              tx.objectStore('queue').clear()
              tx.oncomplete = () => resolve(null)
              tx.onerror = () => resolve(null)
            }
            open.onerror = () => resolve(null)
          }),
      )
      .catch(() => {})
    for (const id of made.tasks) await api(`${P}/tasks/${id}`, { method: 'DELETE' })
    for (const task of (await api(`${P}/search?q=${stamp}&include_done=true`)).body?.tasks ?? []) {
      await api(`${P}/tasks/${task.id}`, { method: 'DELETE' })
    }
    for (const id of made.meals) await api(`/v1/nutrition/meals/${id}`, { method: 'DELETE' })
    for (const id of made.water) await api(`/v1/nutrition/water/${id}`, { method: 'DELETE' })
    if (today) {
      if (journalBefore) {
        const { id: _id, date: _date, created_at: _c, updated_at: _u, ...fields } = journalBefore
        await send('PUT', `${P}/journal/${today}`, fields)
      } else {
        await api(`${P}/journal/${today}`, { method: 'DELETE' })
      }
    }
    const left = (await api(`${P}/search?q=${stamp}&include_done=true`)).body?.tasks ?? []
    const dayLeft = (await api('/v1/nutrition/day')).body
    const mealsLeft = (dayLeft?.meals ?? []).filter((meal) => made.meals.has(meal.client_uuid)).length
    const waterLeft = (dayLeft?.water_logs ?? []).filter((row) => made.water.has(row.id)).length
    check(left.length === 0 && mealsLeft === 0 && waterLeft === 0, 'stage-9 test data removed (tasks, meals, water, journal restored)')
  }
}
