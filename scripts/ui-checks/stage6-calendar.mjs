/**
 * Browser checks — stage 6 calendar (day/week/month, drag), timer, time log
 * and journal. Called by scripts/ui-check.mjs with shared helpers; owned by
 * the calendar/time/journal workstream.
 *
 * Every write the UI makes is confirmed on the API afterwards (polled, so a
 * busy machine is not a failure). Mouse drags drive the desktop path, CDP
 * touch events the iPhone one (long-press, then move; a quick swipe scrolls).
 * Everything the run creates is named "UI-check … <run>" and removed at the
 * end (tasks, time entries, the area); today's journal entry is restored if
 * it existed before the run.
 */
export async function run(ctx) {
  const { page, context, WEB_URL, check, ok, shot, setOffline, allowErrors } = ctx
  console.log('\n7¹². stage 6 calendar, timer & journal')

  const HOUR_PX = 52
  const RUN = String(Date.now()).slice(-6)
  const P = '/v1/planner'
  const apiFetch = (path, init = {}) =>
    page.evaluate(
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
  const post = (path, body) => apiFetch(path, { method: 'POST', body: JSON.stringify(body) })
  const uuid = () => crypto.randomUUID()
  const addDays = (day, n) => {
    const date = new Date(`${day}T12:00:00Z`)
    date.setUTCDate(date.getUTCDate() + n)
    return date.toISOString().slice(0, 10)
  }
  const digits = (value) => String(value ?? '').replace(/\D/g, '')
  const plain = (value) => String(value ?? '').replace(/[   ]/g, ' ')
  /** Server instants carry microseconds; milliseconds are enough here. */
  const ms = (iso) => Date.parse(String(iso ?? '').replace(/(\.\d{3})\d+/, '$1'))
  /** The app's formatMinutes (ru): 45 → "45 мин", 90 → "1 ч 30 мин", 120 → "2 ч". */
  const minutesText = (total) => {
    const h = Math.floor(total / 60)
    const m = total % 60
    return h === 0 ? `${m} мин` : m === 0 ? `${h} ч` : `${h} ч ${m} мин`
  }
  /** Poll `read` until `done(value)` or `limit` ms pass; the last value either way. */
  const until = async (read, done, limit = 8000) => {
    const end = Date.now() + limit
    let value = await read()
    while (!done(value) && Date.now() < end) {
      await page.waitForTimeout(150)
      value = await read()
    }
    return value
  }
  const visible = (locator, limit = 8000) =>
    locator
      .first()
      .waitFor({ timeout: limit })
      .then(() => true)
      .catch(() => false)
  const getTask = async (id) => (await apiFetch(`${P}/tasks/${id}`)).body
  const taskUntil = (id, done) => until(() => getTask(id), (task) => !!task && done(task))
  const blockOf = (task) => (task?.block ? `${task.block.date} ${task.block.start}–${task.block.end}` : 'no block')
  const entriesOn = async (from, to = from) => (await apiFetch(`${P}/time-entries?from=${from}&to=${to}`)).body?.items ?? []
  const entryUntil = (from, to, description, done) =>
    until(
      async () => (await entriesOn(from, to)).filter((entry) => entry.description === description),
      (found) => done(found),
    )
  const journalUntil = (day, done) => until(async () => (await apiFetch(`${P}/journal/${day}`)).body, (body) => !!body && done(body))
  const toast = (needle) => page.locator('div.fixed[role="status"] p', { hasText: needle })
  const sheet = page.locator('[role="dialog"]')
  const closeSheet = async () => {
    const close = sheet.getByRole('button', { name: 'Close' }).first()
    if (await close.count()) await close.click()
    await sheet.first().waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
  }

  /** Scroll the page so `hour` sits right under the calendar's sticky headers. */
  const scrollToHour = async (hour) => {
    await page.evaluate((h) => {
      const hours = document.querySelector('[data-testid="cal-hours"]')
      const sticky = document.querySelector('[data-testid="cal-grid"] > div')
      window.scrollTo(0, hours.getBoundingClientRect().top + window.scrollY + h * 52 - sticky.getBoundingClientRect().bottom)
    }, hour)
    await page.waitForTimeout(250)
  }
  const dragWithMouse = async (from, to) => {
    await page.mouse.move(from.x, from.y)
    await page.mouse.down()
    await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 5 })
    await page.mouse.move(to.x, to.y, { steps: 5 })
    await page.waitForTimeout(80)
    await page.mouse.up()
    await page.waitForTimeout(400)
  }
  // the iPhone path: real touch events (Chromium's own, through CDP)
  const cdp = await context.newCDPSession(page)
  const touch = (type, x, y) =>
    cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }] })

  const created = { tasks: [], area: null }
  const counts = (await apiFetch(`${P}/counts`)).body
  const today = counts?.date
  const settings = (await apiFetch('/v1/settings')).body?.settings ?? {}
  const timeZone = settings.timezone ?? 'UTC'
  /** Minutes after local midnight in the user's timezone. */
  const localNow = () => {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date())
    const value = (type) => Number(parts.find((part) => part.type === type)?.value)
    return value('hour') * 60 + value('minute')
  }
  const journalBefore = today ? (await apiFetch(`${P}/journal/${today}`)).body?.entry ?? null : null

  try {
    check(!!today, 'planner counts answer the server day', today ?? 'no date')

    // ---- data: an area with a weekly target, a block, an all-day task, a daily repeat, two month tasks
    const area = { id: uuid(), name: `UI-check сфера ${RUN}`, weekly_target_minutes: 120, color: '#38E0C8' }
    created.area = (await post(`${P}/areas`, area)).status === 201 ? area.id : null
    const newTaskOn = async (body) => {
      const id = uuid()
      const response = await post(`${P}/tasks`, { id, ...body })
      if (response.status === 201) created.tasks.push(id)
      return response.status === 201 ? id : null
    }
    const titles = {
      block: `UI-check блок ${RUN}`,
      allDay: `UI-check весь день ${RUN}`,
      repeat: `UI-check повтор ${RUN}`,
      month: `UI-check месяц ${RUN}`,
      monthBlock: `UI-check месяц-блок ${RUN}`,
    }
    const blockId = await newTaskOn({ title: titles.block, priority: 2, block: { date: today, start: '10:00', end: '11:00' } })
    // P1 sorts first in the all-day strip, so the narrow week column shows it before «+N»
    const allDayId = await newTaskOn({ title: titles.allDay, priority: 1, due_date: today, due_time: '19:00' })
    const repeatId = await newTaskOn({ title: titles.repeat, recurrence_rule: 'daily', block: { date: today, start: '07:00', end: '07:15' } })
    // two and three days ahead: always inside the month grid of today, away from our other tasks
    const monthFrom = addDays(today, 2)
    const monthTo = addDays(today, 3)
    const monthId = await newTaskOn({ title: titles.month, priority: 1, due_date: monthFrom, due_time: '08:30' })
    const monthBlockId = await newTaskOn({ title: titles.monthBlock, priority: 3, block: { date: monthFrom, start: '15:00', end: '16:00' } })
    check(
      !!created.area && !!blockId && !!allDayId && !!repeatId && !!monthId && !!monthBlockId,
      'test area and tasks created through the API',
    )

    // ---- week grid ---------------------------------------------------------------
    await page.goto(`${WEB_URL}/plan/calendar?view=week`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="cal-grid"][data-view="week"]').waitFor({ timeout: 15000 })
    await page.evaluate(() => {
      window.__newTask = undefined
      window.__openTask = undefined
      window.addEventListener('dsos:plan:new-task', (event) => {
        window.__newTask = event.detail
      })
      window.addEventListener('dsos:plan:open-task', (event) => {
        window.__openTask = event.detail
      })
    })
    await scrollToHour(9)
    await shot('6b-calendar-week')
    const block = page.locator(`[data-testid="cal-block"][data-task-id="${blockId}"]`)
    const layer = page.locator('[data-testid="cal-layer"]')
    const blockBox = await block.boundingBox()
    const layerBox = await layer.boundingBox()
    check(
      !!blockBox && Math.abs(blockBox.y - layerBox.y - 10 * HOUR_PX) < 3 && Math.abs(blockBox.height - HOUR_PX) < 3,
      'week grid: 10:00–11:00 block at 10 h × 52 px, one hour tall',
      blockBox ? `${Math.round(blockBox.y - layerBox.y)} px, ${Math.round(blockBox.height)} px` : 'missing',
    )
    const week = await page
      .locator('[data-testid="cal-day-header"]')
      .evaluateAll((items) => items.map((item) => item.getAttribute('data-date')))
    const todayColumn = week.indexOf(today)
    const columnWidth = layerBox.width / 7
    // within the column (other blocks at that hour put it side by side)
    const columnLeft = layerBox.x + todayColumn * columnWidth
    check(
      todayColumn >= 0 && !!blockBox && blockBox.x >= columnLeft - 1 && blockBox.x + blockBox.width <= columnLeft + columnWidth + 1,
      'week grid: the block sits in today’s column (the week starts on settings.week_start)',
      `${week[0]} … ${week[6]}, week_start ${settings.week_start}`,
    )
    check(
      (await page.locator(`[data-testid="cal-allday-task"][data-task-id="${allDayId}"]`).count()) === 1,
      'all-day strip shows the task due today (no block)',
    )
    const nowLine = await page.locator('[data-testid="cal-now"]').boundingBox()
    const nowMinutes = localNow()
    check(
      !!nowLine &&
        Math.abs(nowLine.y + 1 - layerBox.y - (nowMinutes * HOUR_PX) / 60) < 2.5 &&
        Math.abs(nowLine.x - (layerBox.x + todayColumn * columnWidth)) < 3,
      'current-time line in today’s column at the user’s local time',
      `${timeZone} ${String(Math.floor(nowMinutes / 60)).padStart(2, '0')}:${String(nowMinutes % 60).padStart(2, '0')}`,
    )
    const laterDays = week.filter((day) => day > today).length
    const weekRepeats = await page.locator(`[data-testid="cal-occurrence"][aria-label*="${titles.repeat}"]`).count()
    if (laterDays > 0) {
      check(weekRepeats === laterDays, 'week grid: the daily repeat is drawn on every later day (virtual, dashed)', `${weekRepeats}/${laterDays}`)
    } else {
      ok('week grid: today ends the week — repeats are checked in the month view')
    }

    // mouse drag: one hour later
    const grab = { x: blockBox.x + blockBox.width / 2, y: blockBox.y + 12 }
    await dragWithMouse(grab, { x: grab.x, y: grab.y + HOUR_PX })
    let task = await taskUntil(blockId, (value) => value.block?.start !== '10:00')
    check(
      task?.block?.date === today && task?.block?.start === '11:00' && task?.block?.end === '12:00',
      'mouse drag moves the block 1 h later (API)',
      blockOf(task),
    )
    check((await page.evaluate(() => window.__newTask ?? window.__openTask)) === undefined, 'a drop opens neither the editor nor a new task')

    // resize from the bottom edge: +30 min
    await scrollToHour(9)
    const handle = page.locator(`[data-testid="cal-block"][data-task-id="${blockId}"] [data-testid="cal-resize"]`)
    const handleBox = await handle.boundingBox()
    if (handleBox) {
      const from = { x: handleBox.x + handleBox.width / 2, y: handleBox.y + handleBox.height / 2 }
      await dragWithMouse(from, { x: from.x, y: from.y + HOUR_PX / 2 })
    }
    task = await taskUntil(blockId, (value) => value.block?.end !== '12:00')
    check(task?.block?.start === '11:00' && task?.block?.end === '12:30', 'resize from the bottom edge → 11:00–12:30 (API)', blockOf(task))

    // touch: a quick swipe over the block scrolls the page and moves nothing
    await scrollToHour(8)
    let box = await block.boundingBox()
    const scrolledBefore = await page.evaluate(() => window.scrollY)
    const swipe = { x: box.x + box.width / 2, y: box.y + 10 }
    await touch('touchStart', swipe.x, swipe.y)
    for (let step = 1; step <= 8; step += 1) {
      await touch('touchMove', swipe.x, swipe.y + step * 15)
      await page.waitForTimeout(16)
    }
    await touch('touchEnd', swipe.x, swipe.y + 120)
    await page.waitForTimeout(700)
    const scrolledAfter = await page.evaluate(() => window.scrollY)
    task = await getTask(blockId)
    check(
      scrolledAfter < scrolledBefore - 40 && task?.block?.start === '11:00' && task?.block?.end === '12:30' &&
        (await page.locator('[data-testid="cal-drag-preview"]').count()) === 0,
      'touch: a quick swipe over a block scrolls the page, the block stays (API)',
      `scrolled ${scrolledBefore - scrolledAfter} px, ${blockOf(task)}`,
    )

    // touch: a long-press (≈ 300 ms) picks the block up; 1 h up drops it at 10:00
    await scrollToHour(8)
    box = await block.boundingBox()
    const lift = { x: box.x + box.width / 2, y: box.y + 10 }
    await touch('touchStart', lift.x, lift.y)
    await page.waitForTimeout(450)
    const lifted = await page.locator('[data-testid="cal-drag-preview"]').count()
    for (let step = 1; step <= 13; step += 1) {
      await touch('touchMove', lift.x, lift.y - step * 4)
      await page.waitForTimeout(16)
    }
    // a week column is narrow: the preview stacks start and end on two lines
    const snapped = plain(await page.locator('[data-testid="cal-drag-time"]').innerText().catch(() => ''))
      .replace(/\s+/g, ' ')
      .trim()
    await touch('touchEnd', lift.x, lift.y - HOUR_PX)
    task = await taskUntil(blockId, (value) => value.block?.start !== '11:00')
    check(
      lifted === 1 && /^10:00\s*–?\s*11:30$/.test(snapped) && task?.block?.start === '10:00' && task?.block?.end === '11:30',
      'touch: long-press, then 1 h up — the snapped time follows the finger, the block moves (API)',
      `preview «${snapped}», ${blockOf(task)}`,
    )
    check((await page.evaluate(() => window.__newTask ?? window.__openTask)) === undefined, 'a touch drop opens neither the editor nor a new task')

    // across days (mouse): the time is kept
    const targetColumn = todayColumn < 6 ? todayColumn + 1 : todayColumn - 1
    await scrollToHour(8)
    const moved = await block.boundingBox()
    if (moved) {
      const from = { x: moved.x + moved.width / 2, y: moved.y + 12 }
      await dragWithMouse(from, { x: from.x + (targetColumn - todayColumn) * columnWidth, y: from.y })
    }
    task = await taskUntil(blockId, (value) => value.block?.date !== today)
    check(
      task?.block?.date === week[targetColumn] && task?.block?.start === '10:00' && task?.block?.end === '11:30',
      'drag across days keeps the time, changes the day (API)',
      blockOf(task),
    )

    // tap an empty slot → the editor opens prefilled
    await scrollToHour(14)
    const slotLayer = await layer.boundingBox()
    await page.mouse.click(slotLayer.x + columnWidth * (todayColumn + 0.5), slotLayer.y + 15 * HOUR_PX + 10)
    const slotSheet = await visible(sheet, 5000)
    const prefill = await page.evaluate(() => window.__newTask)
    check(
      prefill?.block?.date === today && prefill.block.start === '15:00' && prefill.block.end === '16:00',
      'tap an empty slot → new task prefilled 15:00–16:00',
      JSON.stringify(prefill ?? null),
    )
    check(slotSheet, 'the task editor sheet opens for the slot')
    await closeSheet()

    // tap a block → its editor (from a fresh page: whatever the editor does on close)
    await page.goto(`${WEB_URL}/plan/calendar?view=week`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="cal-grid"][data-view="week"]').waitFor({ timeout: 15000 })
    await page.evaluate(() => {
      window.__openTask = undefined
      window.addEventListener('dsos:plan:open-task', (event) => {
        window.__openTask = event.detail
      })
    })
    await scrollToHour(6)
    await page.locator(`[data-testid="cal-block"][data-task-id="${repeatId}"]`).click()
    const blockSheet = await visible(sheet, 5000)
    check(blockSheet && (await page.evaluate(() => window.__openTask)) === repeatId, 'tap a block → the editor opens for that task')
    await closeSheet()

    // ---- day view ------------------------------------------------------------------
    await page.goto(`${WEB_URL}/plan/calendar?view=day&date=${today}`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="cal-grid"][data-view="day"]').waitFor({ timeout: 10000 })
    const dayChip = page.locator(`[data-testid="cal-allday-task"][data-task-id="${allDayId}"]`)
    check(plain(await dayChip.innerText()).includes('19:00'), 'day view: the all-day task carries its due_time label')
    const todayTitle = await page.locator('[data-testid="cal-title"]').innerText()
    await page.locator('[aria-label="Следующий день"]').click()
    await until(() => page.locator('[data-testid="cal-title"]').innerText(), (title) => title !== todayTitle, 5000)
    const nextTitle = await page.locator('[data-testid="cal-title"]').innerText()
    await page.locator('[data-testid="cal-toolbar"]').getByRole('button', { name: 'Сегодня' }).click()
    const back = await visible(dayChip, 5000)
    check(
      nextTitle !== todayTitle &&
        back &&
        new URL(page.url()).searchParams.get('date') === null &&
        (await page.locator('[data-testid="cal-title"]').innerText()) === todayTitle,
      'day view: next day, then «Сегодня» brings back the server’s today',
      `${todayTitle} → ${nextTitle}`,
    )

    // ---- month -------------------------------------------------------------------------
    await page.goto(`${WEB_URL}/plan/calendar?view=month&date=${today}`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="cal-month"]').waitFor({ timeout: 10000 })
    check((await page.locator('[data-testid="cal-month-day"]').count()) === 42, 'month: 6×7 grid')
    // (a crowded day hides chips behind «+N», so count the repeat's chips over the whole grid)
    const repeatDays = await page
      .locator('[data-testid="cal-month-day"]', { has: page.locator('[data-testid="cal-month-repeat"]', { hasText: titles.repeat }) })
      .evaluateAll((cells) => cells.map((cell) => cell.getAttribute('data-date')))
    check(
      repeatDays.length > 0 && repeatDays.every((day) => day > today),
      'month: virtual repeats of the daily task, only after today',
      `${repeatDays.length} days from ${repeatDays[0]}`,
    )
    await page.locator(`[data-testid="cal-month-day"][data-date="${monthFrom}"]`).evaluate((element) => element.scrollIntoView({ block: 'center' }))
    await page.waitForTimeout(250)
    await shot('6b-calendar-month')
    const dragChip = async (taskId, day) => {
      const chip = await page.locator(`[data-testid="cal-month-chip"][data-task-id="${taskId}"]`).boundingBox()
      const cell = await page.locator(`[data-testid="cal-month-day"][data-date="${day}"]`).boundingBox()
      if (chip && cell) {
        await dragWithMouse({ x: chip.x + chip.width / 2, y: chip.y + chip.height / 2 }, { x: cell.x + cell.width / 2, y: cell.y + cell.height / 2 })
      }
    }
    await dragChip(monthId, monthTo)
    task = await taskUntil(monthId, (value) => value.due_date !== monthFrom)
    check(
      task?.due_date === monthTo && task?.due_time === '08:30' && task?.block === null,
      'month: dragging a chip to another day changes due_date, keeps due_time (API)',
      `${task?.due_date} ${task?.due_time}`,
    )
    await page.locator(`[data-testid="cal-month-day"][data-date="${monthTo}"] [data-testid="cal-month-chip"][data-task-id="${monthId}"]`).waitFor({ timeout: 5000 }).catch(() => {})
    await dragChip(monthBlockId, monthTo)
    task = await taskUntil(monthBlockId, (value) => value.block?.date !== monthFrom)
    check(
      task?.block?.date === monthTo && task?.block?.start === '15:00' && task?.block?.end === '16:00',
      'month: a chip with a time block moves its block, start and length kept (API)',
      blockOf(task),
    )
    await page.locator(`[data-testid="cal-month-day"][data-date="${monthTo}"]`).click({ position: { x: 8, y: 8 } })
    const agenda = page.locator(`[data-testid="cal-agenda"][data-date="${monthTo}"]`)
    check(await visible(agenda.filter({ hasText: titles.month }).filter({ hasText: '15:00–16:00' }), 5000), 'month: tapping a day lists its tasks below the grid')

    // offline: the chip moves at once, the change waits in the queue and is sent on reconnect
    await setOffline(true)
    await dragChip(monthId, monthFrom)
    const movedOffline =
      (await visible(page.locator(`[data-testid="cal-month-day"][data-date="${monthFrom}"] [data-testid="cal-month-chip"][data-task-id="${monthId}"]`), 3000)) &&
      (await visible(toast('перенос сохранится'), 3000))
    await setOffline(false)
    task = await taskUntil(monthId, (value) => value.due_date === monthFrom)
    check(
      movedOffline && task?.due_date === monthFrom && task?.due_time === '08:30',
      'month: a chip moved offline shows at once, is queued, and reaches the server on reconnect (API)',
      `${task?.due_date} ${task?.due_time}`,
    )

    // ---- timer -------------------------------------------------------------------------
    const foreign = (await apiFetch(`${P}/timer`)).body?.running
    if (foreign) await post(`${P}/timer/stop`, { entry_id: foreign.entry.id })
    await page.goto(`${WEB_URL}/plan`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="timer-start"]').click()
    await page.locator('#timer-description').fill(`UI-check таймер ${RUN}`)
    if (created.area) await page.locator('#timer-area').selectOption(created.area)
    await page.locator('[data-testid="timer-start-submit"]').click()
    await page.locator('[data-testid="timer-bar"]').waitFor({ timeout: 10000 })
    await entryUntil(today, today, `UI-check таймер ${RUN}`, (found) => found.length === 1)
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('[data-testid="timer-bar"]').waitFor({ timeout: 10000 })
    const tick1 = await page.locator('[data-testid="timer-elapsed"]').innerText()
    await page.waitForTimeout(1600)
    const tick2 = await page.locator('[data-testid="timer-elapsed"]').innerText()
    check(
      tick1 !== tick2 && (await page.locator('[data-testid="timer-title"]').innerText()) === `UI-check таймер ${RUN}`,
      'timer survives a reload and keeps ticking',
      `${tick1} → ${tick2}`,
    )
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    check(await visible(page.locator('[data-testid="timer-bar"]'), 5000), 'Today shows the running timer')
    await page.goto(`${WEB_URL}/plan/time`, { waitUntil: 'networkidle' })
    check(await visible(page.locator('[data-testid="time-running"]'), 5000), 'time log pins the running entry')
    const shown = await page.locator('[data-testid="timer-elapsed"]').innerText()
    await page.locator('[data-testid="timer-stop"]').click()
    const [mm, ss] = shown.split(':').map(Number)
    const [timed] = await entryUntil(today, today, `UI-check таймер ${RUN}`, (found) => found.length === 1 && !found[0].running)
    check(
      !!timed && !timed.running && timed.duration_s >= mm * 60 + ss && timed.duration_s < mm * 60 + ss + 10 && timed.area?.id === created.area,
      'stop → a finished entry as long as the bar showed, with its area (API)',
      timed ? `${timed.duration_s} s (bar ${shown})` : 'missing',
    )
    check(
      (await visible(page.locator('[data-testid="time-day"]').filter({ hasText: `UI-check таймер ${RUN}` }), 5000)) &&
        (await visible(page.locator('[data-testid="timer-start"]'), 5000)),
      'the stopped entry is listed in the time log, the bar is idle again',
    )

    // offline: the start waits in the queue with the tap instant, then reaches the server once
    // (the sheet opens online: the dev server has no service worker to serve its chunk offline)
    await page.locator('[data-testid="timer-start"]').click()
    await page.locator('#timer-description').fill(`UI-check офлайн-таймер ${RUN}`)
    await setOffline(true)
    const tappedAt = Date.now()
    await page.locator('[data-testid="timer-start-submit"]').click()
    const offlineBar =
      (await visible(page.locator('[data-testid="timer-title"]').filter({ hasText: `UI-check офлайн-таймер ${RUN}` }), 5000)) &&
      (await visible(toast('Нет сети'), 3000))
    await page.waitForTimeout(1200)
    await setOffline(false)
    const offlineEntries = await entryUntil(addDays(today, -1), today, `UI-check офлайн-таймер ${RUN}`, (found) => found.length > 0)
    const offlineStart = offlineEntries[0] ? ms(offlineEntries[0].started_at) : Number.NaN
    check(
      offlineBar && offlineEntries.length === 1 && offlineEntries[0].running && Math.abs(offlineStart - tappedAt) < 10_000,
      'offline start: the bar runs at once, the queued start reaches the server once, at the tap time (API)',
      offlineEntries[0] ? `${offlineEntries.length} entry, started ${Math.round((offlineStart - tappedAt) / 1000)} s from the tap` : 'not sent',
    )
    await page.locator('[data-testid="timer-bar"]').filter({ hasText: `UI-check офлайн-таймер ${RUN}` }).waitFor({ timeout: 5000 }).catch(() => {})
    await page.locator('[data-testid="timer-stop"]').click()
    const [offlineStopped] = await entryUntil(
      addDays(today, -1),
      today,
      `UI-check офлайн-таймер ${RUN}`,
      (found) => found.length === 1 && !found[0].running,
    )
    check(!!offlineStopped && !offlineStopped.running && offlineStopped.duration_s >= 1, 'that timer then stops online (API)', `${offlineStopped?.duration_s} s`)

    // a timer left running for over a day: the stop button asks how long it really ran
    const serverNow = (await apiFetch(`${P}/timer`)).body?.server_now
    const longStart = await post(`${P}/timer/start`, {
      id: uuid(),
      description: `UI-check сутки ${RUN}`,
      started_at: new Date(ms(serverNow) - 24 * 3600_000 + 8000).toISOString(),
    })
    await until(async () => (await apiFetch(`${P}/timer`)).body?.running?.elapsed_s ?? 0, (elapsed) => elapsed > 24 * 3600, 30_000)
    await page.goto(`${WEB_URL}/plan`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="timer-bar"]').waitFor({ timeout: 10000 })
    const warning = await page.locator('[data-testid="timer-bar"] p[role="status"]').innerText().catch(() => '')
    await page.locator('[data-testid="timer-stop"]').click()
    const asked = await visible(page.locator('[data-testid="timer-duration-sheet"]'), 5000)
    await page.locator('#timer-hours').fill('1')
    await page.locator('#timer-minutes').fill('30')
    await shot('6b-timer-over-a-day')
    await page.getByRole('button', { name: 'Остановить и записать' }).click()
    const [longEntry] = await entryUntil(addDays(today, -1), today, `UI-check сутки ${RUN}`, (found) => found.length === 1 && !found[0].running)
    check(
      longStart.status === 200 && warning.includes('больше суток') && asked && !!longEntry && !longEntry.running && longEntry.duration_s === 5400,
      'a timer over 24 h warns and asks how long it ran: saved as 1 h 30 min (API)',
      longEntry ? `${longEntry.duration_s} s` : `start ${longStart.status}, warning «${warning}», sheet ${asked}`,
    )

    // manual entry: 45 minutes that already happened (yesterday's when it is not 01:00 yet)
    const manualDay = localNow() >= 60 ? today : addDays(today, -1)
    await page.goto(`${WEB_URL}/plan/time?date=${manualDay}`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="time-add-manual"]').click()
    await page.locator('#te-date').fill(manualDay)
    await page.locator('#te-start').fill('00:05')
    await page.locator('#te-end').fill('00:50')
    await page.locator('#te-description').fill(`UI-check вручную ${RUN}`)
    if (created.area) await page.locator('#te-area').selectOption(created.area)
    check(plain(await page.locator('[data-testid="te-duration"]').innerText()).includes('45 мин'), 'manual entry previews 45 min')
    await page.locator('[data-testid="time-entry-save"]').click()
    const [manual] = await entryUntil(manualDay, manualDay, `UI-check вручную ${RUN}`, (found) => found.length > 0)
    check(
      manual?.start === '00:05' && manual?.end === '00:50' && manual?.duration_s === 2700 && manual?.source === 'manual',
      'manual entry saved as local 00:05–00:50, 45 min (API)',
      JSON.stringify(manual ? { start: manual.start, end: manual.end, s: manual.duration_s } : null),
    )
    await sheet.first().waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})

    // the server refuses an entry in the future: the sheet stays open and says why
    // (the browser logs that 422; allowErrors keeps it out of the console check)
    if (allowErrors) {
      await page.locator('[data-testid="time-add-manual"]').click()
      await page.locator('#te-date').fill(addDays(today, 1))
      await page.locator('#te-start').fill('10:00')
      await page.locator('#te-end').fill('11:00')
      await page.locator('#te-description').fill(`UI-check будущее ${RUN}`)
      const refused = await allowErrors(async () => {
        await page.locator('[data-testid="time-entry-save"]').click()
        return visible(toast('не в будущем'), 6000)
      })
      const future = (await entriesOn(addDays(today, 1))).some((entry) => entry.description === `UI-check будущее ${RUN}`)
      check(
        refused && (await page.locator('[data-testid="time-entry-sheet"]').count()) === 1 && !future,
        'an entry in the future: the 422 is shown, the sheet stays open, nothing saved (API)',
      )
      await closeSheet()
    } else {
      ok('an entry in the future — skipped (this runner has no allowErrors for the expected 422)')
    }

    // edit the manual entry: end 01:00 → 55 min
    const manualRow = page.locator('[data-testid="time-day"] button', { hasText: `UI-check вручную ${RUN}` })
    await manualRow.click()
    await page.locator('#te-end').fill('01:00')
    await page.locator('[data-testid="time-entry-save"]').click()
    const [edited] = await entryUntil(manualDay, manualDay, `UI-check вручную ${RUN}`, (found) => found[0]?.end === '01:00')
    check(
      edited?.id === manual?.id && edited?.end === '01:00' && edited?.duration_s === 3300,
      'editing the entry’s end saves 55 min (API)',
      `${edited?.start}–${edited?.end}`,
    )

    // summary: the area against its 2 h target, the total as the server counts them
    await page.reload({ waitUntil: 'networkidle' })
    const summary = (await apiFetch(`${P}/time/summary?period=week&date=${manualDay}`)).body
    const areaMinutes = summary?.by_area?.find((row) => row.area?.id === created.area)?.minutes ?? 0
    const areaRow = page.locator('[data-testid="time-area-row"]').filter({ hasText: `UI-check сфера ${RUN}` })
    const areaText = (await visible(areaRow, 5000)) ? plain(await areaRow.innerText()).replace(/\s+/g, ' ') : ''
    check(
      areaMinutes > 0 &&
        areaText.includes(`${minutesText(areaMinutes)} из 2 ч`) &&
        plain(await page.locator('[data-testid="time-total"]').innerText()) === minutesText(summary.total_minutes),
      'summary: the total and the area against its 2 h target, as the server counts them',
      areaText,
    )
    await shot('6b-time-log')

    // delete the manual entry from its sheet
    await manualRow.click()
    await page.locator('[data-testid="time-entry-delete"]').click()
    await page.locator('[data-testid="time-entry-delete-confirm"]').click()
    const remaining = await entryUntil(manualDay, manualDay, `UI-check вручную ${RUN}`, (found) => found.length === 0)
    await manualRow.waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})
    check(remaining.length === 0 && (await manualRow.count()) === 0, 'deleting an entry (after confirming) removes it (API)')

    // ---- journal -------------------------------------------------------------------------
    await page.goto(`${WEB_URL}/plan/journal`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="journal-scales"]').waitFor({ timeout: 10000 })
    const status = page.locator('[data-testid="journal-status"]')
    const wins = `UI-check **победа** ${RUN}`
    await page.locator('[data-testid="journal-wins"]').fill(wins)
    await page.locator('[data-testid="journal-rating-4"]').click()
    await page.locator('[data-testid="journal-mood-3"]').click()
    check(await visible(page.locator('[data-testid="journal-status"][data-status="saved"]'), 8000), 'journal: autosave status «Сохранено»')
    check(
      await visible(page.locator(`[data-testid="journal-strip-day"][data-date="${today}"][data-has-entry="true"]`), 5000),
      'journal: the month strip marks today as written',
    )
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('[data-testid="journal-wins"]').waitFor({ timeout: 10000 })
    check((await page.locator('[data-testid="journal-wins"]').inputValue()) === wins, 'journal: autosaved text survives a reload')
    await page.locator('[data-testid="journal-wins-toggle"]').click()
    check(
      (await page.locator('[data-testid="journal-field-wins"] strong', { hasText: 'победа' }).count()) === 1,
      'journal: Preview renders the Markdown',
    )
    await page.locator('[data-testid="journal-wins-toggle"]').click()
    const saved = (await apiFetch(`${P}/journal/${today}`)).body
    check(
      saved?.entry?.rating === 4 && saved?.entry?.mood === 3 && saved?.entry?.wins_md === wins,
      'journal: text, rating and mood saved (API)',
      `${saved?.entry?.rating}/${saved?.entry?.mood}`,
    )
    const day = saved?.day
    const food = await page.locator('[data-testid="journal-food-value"]').innerText()
    const burned = await page.locator('[data-testid="journal-burned-value"]').innerText()
    const time = plain(await page.locator('[data-testid="journal-time-value"]').innerText())
    check(
      digits(food) === digits(Math.round(day?.kcal ?? 0)) &&
        (day?.workout_kcal == null ? burned.includes('—') : digits(burned) === digits(Math.round(day.workout_kcal))) &&
        time === minutesText(day?.time_minutes ?? 0),
      'journal: food kcal and burned kcal on separate tiles, tracked time — as the server says',
      `${plain(food)} | ${plain(burned)} | ${time}`,
    )
    await page.locator('[data-testid="journal-summary"]').scrollIntoViewIfNeeded()
    await shot('6b-journal-summary')

    // offline edit → queued → sent
    await setOffline(true)
    const better = `UI-check офлайн ${RUN}`
    await page.locator('[data-testid="journal-improvements"]').fill(better)
    const queued = await visible(page.locator('[data-testid="journal-status"][data-status="queued"]'), 6000)
    await setOffline(false)
    const sent = await visible(page.locator('[data-testid="journal-status"][data-status="saved"]'), 10000)
    const afterSync = await journalUntil(today, (body) => body.entry?.improvements_md === better)
    check(
      queued && sent && afterSync?.entry?.improvements_md === better,
      'journal: an offline edit is queued, then sent',
      `${queued ? 'queued' : 'not queued'} → ${(await status.getAttribute('data-status')) ?? '?'}`,
    )

    // delete the day's entry (asks first)
    await page.locator('[data-testid="journal-delete"]').click()
    await page.locator('[data-testid="journal-delete-confirm"]').click()
    const gone = await journalUntil(today, (body) => body.entry === null)
    check(
      gone?.entry === null && (await page.locator('[data-testid="journal-wins"]').inputValue()) === '',
      'journal: deleting the day’s entry (after confirming) clears it (API)',
    )
  } catch (error) {
    check(false, 'stage 6 calendar checks ran to the end', String(error?.message ?? error))
  } finally {
    // ---- clean up: tasks, this run's time entries, the area, today's journal -------
    await setOffline(false)
    for (const id of created.tasks) await apiFetch(`${P}/tasks/${id}`, { method: 'DELETE' })
    const running = (await apiFetch(`${P}/timer`)).body?.running
    if (running?.entry?.description?.includes(RUN)) await post(`${P}/timer/stop`, { entry_id: running.entry.id })
    if (today) {
      for (const entry of await entriesOn(addDays(today, -1), addDays(today, 1))) {
        if (entry.description?.includes(RUN)) await apiFetch(`${P}/time-entries/${entry.id}`, { method: 'DELETE' })
      }
      if (journalBefore) {
        const { rating, mood, energy, wins_md, improvements_md, note_md, tags } = journalBefore
        await apiFetch(`${P}/journal/${today}`, {
          method: 'PUT',
          body: JSON.stringify({ rating, mood, energy, wins_md, improvements_md, note_md, tags }),
        })
      } else if ((await apiFetch(`${P}/journal/${today}`)).body?.entry) {
        await apiFetch(`${P}/journal/${today}`, { method: 'DELETE' })
      }
    }
    if (created.area) await apiFetch(`${P}/areas/${created.area}`, { method: 'DELETE' })
    const left = today ? (await apiFetch(`${P}/views/calendar?from=${addDays(today, -7)}&to=${addDays(today, 14)}`)).body : null
    const leftTasks = (left?.days ?? []).some((item) =>
      [...item.blocks, ...item.all_day, ...item.occurrences].some((entry) => entry.title?.includes(RUN)),
    )
    const leftEntries = today ? (await entriesOn(addDays(today, -1), addDays(today, 1))).some((entry) => entry.description?.includes(RUN)) : false
    const areaGone = !created.area || (await apiFetch(`${P}/areas/${created.area}`)).status === 404
    const journalNow = today ? (await apiFetch(`${P}/journal/${today}`)).body?.entry ?? null : null
    const journalRestored = journalBefore ? journalNow?.wins_md === journalBefore.wins_md : journalNow === null
    const timerLeft = (await apiFetch(`${P}/timer`)).body?.running?.entry?.description?.includes(RUN)
    check(
      !leftTasks && !leftEntries && areaGone && journalRestored && !timerLeft,
      'stage 6 calendar test data removed (tasks, time entries, area, timer); journal as before',
    )
  }
}
