/**
 * Browser checks — stage 6 calendar (day/week/month, drag), timer, time log
 * and journal. Called by scripts/ui-check.mjs with shared helpers; owned by
 * the calendar/time/journal workstream.
 *
 * Everything it creates is named "UI-check …" and removed at the end; today's
 * journal entry is restored if it existed before the run.
 */
export async function run(ctx) {
  const { page, WEB_URL, check, shot, setOffline } = ctx
  console.log('\n7¹². stage 6 calendar, timer & journal')

  const HOUR_PX = 52
  const RUN = String(Date.now()).slice(-6)
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
  const P = '/v1/planner'
  const uuid = () => crypto.randomUUID()
  const addDays = (day, n) => {
    const date = new Date(`${day}T12:00:00Z`)
    date.setUTCDate(date.getUTCDate() + n)
    return date.toISOString().slice(0, 10)
  }
  const digits = (value) => String(value ?? '').replace(/\D/g, '')
  /** Scroll the page so `hour` sits right under the calendar's sticky headers. */
  const scrollToHour = (hour) =>
    page.evaluate((h) => {
      const hours = document.querySelector('[data-testid="cal-hours"]')
      const sticky = document.querySelector('[data-testid="cal-grid"] > div')
      window.scrollTo(0, hours.getBoundingClientRect().top + window.scrollY + h * 52 - sticky.getBoundingClientRect().bottom)
    }, hour)
  const dragWithMouse = async (from, to) => {
    await page.mouse.move(from.x, from.y)
    await page.mouse.down()
    await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 5 })
    await page.mouse.move(to.x, to.y, { steps: 5 })
    await page.waitForTimeout(80)
    await page.mouse.up()
    await page.waitForTimeout(700)
  }
  const getTask = async (id) => (await apiFetch(`${P}/tasks/${id}`)).body

  const created = { tasks: [], area: null }
  const counts = (await apiFetch(`${P}/counts`)).body
  const today = counts?.date
  const settings = (await apiFetch('/v1/settings')).body?.settings ?? {}
  const timeZone = settings.timezone ?? 'UTC'
  const localHour = Number(
    new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', hourCycle: 'h23' }).format(new Date()),
  )
  const journalBefore = today ? (await apiFetch(`${P}/journal/${today}`)).body?.entry ?? null : null

  try {
    check(!!today, 'planner counts answer the server day', today ?? 'no date')

    // ---- data: an area with a weekly target, a block, an all-day task, a daily repeat, a month task
    const area = { id: uuid(), name: `UI-check сфера ${RUN}`, weekly_target_minutes: 120, color: '#38E0C8' }
    created.area = (await apiFetch(`${P}/areas`, { method: 'POST', body: JSON.stringify(area) })).status === 201 ? area.id : null
    const newTaskOn = async (body) => {
      const id = uuid()
      const response = await apiFetch(`${P}/tasks`, { method: 'POST', body: JSON.stringify({ id, ...body }) })
      if (response.status === 201) created.tasks.push(id)
      return response.status === 201 ? id : null
    }
    const blockId = await newTaskOn({ title: `UI-check блок ${RUN}`, priority: 2, block: { date: today, start: '10:00', end: '11:00' } })
    const allDayId = await newTaskOn({ title: `UI-check весь день ${RUN}`, due_date: today, due_time: '19:00' })
    const repeatId = await newTaskOn({ title: `UI-check повтор ${RUN}`, recurrence_rule: 'daily', block: { date: today, start: '07:00', end: '07:15' } })
    // two days ahead: a cell without other tasks, so the chip is never hidden behind «+N»
    const monthFrom = addDays(today, 2)
    const monthTo = addDays(today, 3)
    const monthId = await newTaskOn({ title: `UI-check месяц ${RUN}`, priority: 1, due_date: monthFrom })
    check(!!created.area && !!blockId && !!allDayId && !!repeatId && !!monthId, 'test area and tasks created through the API')

    // ---- week grid ---------------------------------------------------------------
    await page.goto(`${WEB_URL}/plan/calendar?view=week`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="cal-grid"]').waitFor({ timeout: 15000 })
    await page.evaluate(() => {
      window.__newTask = undefined
      window.addEventListener('dsos:plan:new-task', (event) => {
        window.__newTask = event.detail
      })
    })
    await scrollToHour(9)
    await page.waitForTimeout(300)
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
    check((await page.locator(`[data-testid="cal-allday-task"][data-task-id="${allDayId}"]`).count()) === 1, 'all-day strip shows the task due today')
    check((await page.locator('[data-testid="cal-now"]').count()) === 1, 'current-time line in today’s column')

    // mouse drag: one hour later
    const grab = { x: blockBox.x + blockBox.width / 2, y: blockBox.y + 12 }
    await dragWithMouse(grab, { x: grab.x, y: grab.y + HOUR_PX })
    let task = await getTask(blockId)
    check(task?.block?.start === '11:00' && task?.block?.end === '12:00' && task?.block?.date === today, 'mouse drag moves the block 1 h later (API)', JSON.stringify(task?.block ?? null))
    check((await page.evaluate(() => window.__newTask)) === undefined, 'a drop does not open the new-task editor')

    // resize from the bottom edge: +30 min
    await scrollToHour(10)
    await page.waitForTimeout(200)
    const handle = page.locator(`[data-testid="cal-block"][data-task-id="${blockId}"] [data-testid="cal-resize"]`)
    const handleBox = await handle.boundingBox()
    if (handleBox) {
      const from = { x: handleBox.x + handleBox.width / 2, y: handleBox.y + handleBox.height / 2 }
      await dragWithMouse(from, { x: from.x, y: from.y + HOUR_PX / 2 })
    }
    task = await getTask(blockId)
    check(task?.block?.start === '11:00' && task?.block?.end === '12:30', 'resize from the bottom edge → 11:00–12:30 (API)', JSON.stringify(task?.block ?? null))

    // across days
    const week = await page.locator('[data-testid="cal-day-header"]').evaluateAll((items) => items.map((item) => item.getAttribute('data-date')))
    const todayColumn = week.indexOf(today)
    const targetColumn = todayColumn < 6 ? todayColumn + 1 : todayColumn - 1
    const moved = await block.boundingBox()
    const columnWidth = (await layer.boundingBox()).width / 7
    if (moved) {
      const from = { x: moved.x + moved.width / 2, y: moved.y + 12 }
      await dragWithMouse(from, { x: from.x + (targetColumn - todayColumn) * columnWidth, y: from.y })
    }
    task = await getTask(blockId)
    check(task?.block?.date === week[targetColumn] && task?.block?.start === '11:00', 'drag across days keeps the time, changes the day (API)', JSON.stringify(task?.block ?? null))

    // tap an empty slot → the editor opens prefilled
    await scrollToHour(14)
    await page.waitForTimeout(200)
    const slotLayer = await layer.boundingBox()
    await page.mouse.click(slotLayer.x + columnWidth * (todayColumn + 0.5), slotLayer.y + 15 * HOUR_PX + 10)
    await page.waitForTimeout(500)
    const prefill = await page.evaluate(() => window.__newTask)
    check(prefill?.block?.date === today && prefill.block.start === '15:00' && prefill.block.end === '16:00', 'tap an empty slot → new task prefilled 15:00–16:00', JSON.stringify(prefill ?? null))
    check((await page.locator('[role="dialog"]').count()) > 0, 'the task editor sheet opens for the slot')
    // close it on its backdrop (top of the screen, above any sheet)
    await page.mouse.click(195, 24)
    await page.waitForTimeout(500)

    // ---- day + month -------------------------------------------------------------
    await page.goto(`${WEB_URL}/plan/calendar?view=day&date=${today}`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="cal-grid"][data-view="day"]').waitFor({ timeout: 10000 })
    check((await page.locator(`[data-testid="cal-allday-task"][data-task-id="${allDayId}"]`).innerText()).includes('19:00'), 'day view: the all-day task carries its due_time label')

    await page.goto(`${WEB_URL}/plan/calendar?view=month&date=${today}`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="cal-month"]').waitFor({ timeout: 10000 })
    check((await page.locator('[data-testid="cal-month-day"]').count()) === 42, 'month: 6×7 grid')
    check((await page.locator('[data-testid="cal-month-repeat"]').count()) > 0, 'month: virtual repeats of the daily task rendered')
    const target = monthTo
    await page.locator(`[data-testid="cal-month-day"][data-date="${monthFrom}"]`).evaluate((element) => element.scrollIntoView({ block: 'center' }))
    await page.waitForTimeout(200)
    await shot('6b-calendar-month')
    const chip = await page.locator(`[data-testid="cal-month-chip"][data-task-id="${monthId}"]`).boundingBox()
    const cell = await page.locator(`[data-testid="cal-month-day"][data-date="${target}"]`).boundingBox()
    if (chip && cell) {
      await dragWithMouse({ x: chip.x + chip.width / 2, y: chip.y + chip.height / 2 }, { x: cell.x + cell.width / 2, y: cell.y + cell.height / 2 })
    }
    task = await getTask(monthId)
    check(task?.due_date === target && task?.due_time === null, 'month: dragging a chip to another day changes due_date (API)', `${task?.due_date} ${task?.due_time}`)

    // ---- timer -------------------------------------------------------------------
    if ((await apiFetch(`${P}/timer`)).body?.running) await apiFetch(`${P}/timer/stop`, { method: 'POST', body: '{}' })
    await page.goto(`${WEB_URL}/plan`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="timer-start"]').click()
    await page.locator('#timer-description').fill(`UI-check таймер ${RUN}`)
    if (created.area) await page.locator('#timer-area').selectOption(created.area)
    await page.locator('[data-testid="timer-start-submit"]').click()
    await page.locator('[data-testid="timer-bar"]').waitFor({ timeout: 10000 })
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('[data-testid="timer-bar"]').waitFor({ timeout: 10000 })
    const tick1 = await page.locator('[data-testid="timer-elapsed"]').innerText()
    await page.waitForTimeout(1600)
    const tick2 = await page.locator('[data-testid="timer-elapsed"]').innerText()
    check(tick1 !== tick2, 'timer survives a reload and keeps ticking', `${tick1} → ${tick2}`)
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    check((await page.locator('[data-testid="timer-bar"]').count()) === 1, 'Today shows the running timer')
    await page.goto(`${WEB_URL}/plan/time`, { waitUntil: 'networkidle' })
    check((await page.locator('[data-testid="time-running"]').count()) === 1, 'time log pins the running entry')
    await page.locator('[data-testid="timer-stop"]').click()
    await page.waitForTimeout(1200)
    const entries = (await apiFetch(`${P}/time-entries?from=${today}&to=${today}`)).body?.items ?? []
    const timed = entries.find((entry) => entry.description === `UI-check таймер ${RUN}`)
    check(!!timed && !timed.running && timed.duration_s >= 1 && timed.duration_s < 120 && timed.area?.id === created.area, 'stop → a finished entry with the right duration and area (API)', timed ? `${timed.duration_s} s` : 'missing')
    check((await page.locator('[data-testid="time-day"]').first().innerText()).includes(`UI-check таймер ${RUN}`), 'the stopped entry is listed in the time log')

    // manual entry: 45 minutes that already happened
    const manualDay = localHour >= 1 ? today : addDays(today, -1)
    await page.locator('[data-testid="time-add-manual"]').click()
    await page.locator('#te-date').fill(manualDay)
    await page.locator('#te-start').fill('00:05')
    await page.locator('#te-end').fill('00:50')
    await page.locator('#te-description').fill(`UI-check вручную ${RUN}`)
    if (created.area) await page.locator('#te-area').selectOption(created.area)
    check((await page.locator('[data-testid="te-duration"]').innerText()).includes('45'), 'manual entry previews 45 min')
    await page.locator('[data-testid="time-entry-save"]').click()
    await page.waitForTimeout(1200)
    const manual = ((await apiFetch(`${P}/time-entries?from=${manualDay}&to=${manualDay}`)).body?.items ?? []).find(
      (entry) => entry.description === `UI-check вручную ${RUN}`,
    )
    check(manual?.start === '00:05' && manual?.end === '00:50' && manual?.duration_s === 2700, 'manual entry saved as local 00:05–00:50, 45 min (API)', JSON.stringify(manual ? { start: manual.start, end: manual.end, s: manual.duration_s } : null))
    await page.reload({ waitUntil: 'networkidle' })
    const areaRow = page.locator('[data-testid="time-area-row"]').filter({ hasText: `UI-check сфера ${RUN}` })
    check((await areaRow.count()) === 1 && (await areaRow.innerText()).includes('2 ч'), 'summary by area shows the area against its 2 h target')
    await shot('6b-time-log')

    // ---- journal -----------------------------------------------------------------
    await page.goto(`${WEB_URL}/plan/journal`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="journal-scales"]').waitFor({ timeout: 10000 })
    const wins = `UI-check победа ${RUN}`
    await page.locator('[data-testid="journal-wins"]').fill(wins)
    await page.locator('[data-testid="journal-rating-4"]').click()
    await page.locator('[data-testid="journal-mood-3"]').click()
    await page.waitForTimeout(1800)
    check((await page.locator('[data-testid="journal-status"]').getAttribute('data-status')) === 'saved', 'journal: autosave status «Сохранено»')
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('[data-testid="journal-wins"]').waitFor({ timeout: 10000 })
    check((await page.locator('[data-testid="journal-wins"]').inputValue()) === wins, 'journal: autosaved text survives a reload')
    const saved = (await apiFetch(`${P}/journal/${today}`)).body
    check(saved?.entry?.rating === 4 && saved?.entry?.mood === 3, 'journal: rating and mood saved (API)', `${saved?.entry?.rating}/${saved?.entry?.mood}`)
    const day = saved?.day
    const food = await page.locator('[data-testid="journal-food-value"]').innerText()
    const burned = await page.locator('[data-testid="journal-burned-value"]').innerText()
    check(
      digits(food) === digits(Math.round(day?.kcal ?? 0)) &&
        (day?.workout_kcal == null ? burned.includes('—') : digits(burned) === digits(Math.round(day.workout_kcal))),
      'journal: food kcal and burned kcal on separate tiles, as the server says',
      `${food} | ${burned}`,
    )
    await page.locator('[data-testid="journal-summary"]').scrollIntoViewIfNeeded()
    await shot('6b-journal-summary')

    // offline edit → queued → sent
    await setOffline(true)
    const better = `UI-check офлайн ${RUN}`
    await page.locator('[data-testid="journal-improvements"]').fill(better)
    await page.waitForTimeout(1500)
    const queuedStatus = await page.locator('[data-testid="journal-status"]').getAttribute('data-status')
    await setOffline(false)
    await page.waitForTimeout(3000)
    const sentStatus = await page.locator('[data-testid="journal-status"]').getAttribute('data-status')
    const afterSync = (await apiFetch(`${P}/journal/${today}`)).body?.entry
    check(queuedStatus === 'queued' && sentStatus === 'saved' && afterSync?.improvements_md === better, 'journal: an offline edit is queued, then sent', `${queuedStatus} → ${sentStatus}`)
  } catch (error) {
    check(false, 'stage 6 calendar checks ran to the end', String(error?.message ?? error))
  } finally {
    // ---- clean up: tasks, this run's time entries, the area, today's journal -------
    for (const id of created.tasks) await apiFetch(`${P}/tasks/${id}`, { method: 'DELETE' })
    const running = (await apiFetch(`${P}/timer`)).body?.running
    if (running?.entry?.description?.startsWith('UI-check')) await apiFetch(`${P}/timer/stop`, { method: 'POST', body: '{}' })
    if (today) {
      const range = (await apiFetch(`${P}/time-entries?from=${addDays(today, -1)}&to=${today}`)).body?.items ?? []
      for (const entry of range) {
        if (entry.description?.startsWith('UI-check')) await apiFetch(`${P}/time-entries/${entry.id}`, { method: 'DELETE' })
      }
      if (journalBefore) {
        const { rating, mood, energy, wins_md, improvements_md, note_md } = journalBefore
        await apiFetch(`${P}/journal/${today}`, {
          method: 'PUT',
          body: JSON.stringify({ rating, mood, energy, wins_md, improvements_md, note_md }),
        })
      } else {
        await apiFetch(`${P}/journal/${today}`, { method: 'DELETE' })
      }
    }
    if (created.area) await apiFetch(`${P}/areas/${created.area}`, { method: 'DELETE' })
    const left = today ? (await apiFetch(`${P}/views/calendar?from=${addDays(today, -1)}&to=${addDays(today, 7)}`)).body : null
    const leftover = (left?.days ?? []).some((day) =>
      [...day.blocks, ...day.all_day].some((item) => item.title?.includes(`UI-check`) && item.title.includes(RUN)),
    )
    check(!leftover, 'stage 6 calendar test data removed')
  }
}
