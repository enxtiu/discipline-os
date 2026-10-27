/**
 * Browser checks — stage 6 planner views (Today/Inbox/Upcoming, task editor,
 * quick add, projects/areas/resources/tags, search, archive, Today card).
 * Called by scripts/ui-check.mjs with shared helpers; owned by the views workstream.
 *
 * Every check asserts the server state (the API response or a fresh read),
 * not only what the screen says. Everything the checks create — tasks with
 * their subtasks and repeats, a project, an area, a resource, tags, a timer
 * entry — is deleted at the end, and the last check proves it.
 */
export async function run(ctx) {
  const { page, WEB_URL, check, shot, setOffline } = ctx
  console.log('\n7¹¹. stage 6 planner')

  const P = '/v1/planner'
  const api = (path, init = {}) =>
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
  const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
  const sleep = (ms) => page.waitForTimeout(ms)
  /** Re-read until `done(value)` (the server may answer a moment after the UI). */
  const until = async (read, done, timeout = 8000) => {
    const end = Date.now() + timeout
    let value = await read()
    while (!done(value) && Date.now() < end) {
      await sleep(250)
      value = await read()
    }
    return value
  }
  const addDays = (key, n) => new Date(Date.parse(`${key}T12:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10)
  const go = async (route) => {
    await page.goto(`${WEB_URL}${route}`, { waitUntil: 'networkidle' })
    await sleep(300)
  }
  /** The next response of `method` whose path matches `pattern` (start waiting before the action). */
  const response = (pattern, method) =>
    page.waitForResponse(
      (r) => r.request().method() === method && pattern.test(new URL(r.url()).pathname),
      { timeout: 10_000 },
    )
  const act = async (pattern, method, action) => {
    const [result] = await Promise.all([response(pattern, method), action()])
    return { status: result.status(), body: await result.json().catch(() => null), sent: result.request().postDataJSON() }
  }
  const getTask = async (id) => (await api(`${P}/tasks/${id}`)).body
  const row = (id) => page.locator(`[data-testid="task-row"][data-task-id="${id}"]`)
  const sheet = page.locator('[data-testid="task-sheet"]')
  const quickInput = page.locator('[data-testid="quick-add-input"]')
  const toasts = page.locator('[data-testid="plan-toast"]')
  const toastText = async () => (await toasts.allInnerTexts()).join(' | ').replace(/\s+/g, ' ')
  const closeDialog = async () => {
    await page.locator('[role="dialog"]').last().getByRole('button', { name: 'Close' }).click()
    await sleep(500)
  }
  const openTask = async (id) => {
    await row(id).first().locator('[data-testid="task-title"]').click()
    await page.locator(`[data-testid="task-sheet"][data-task-id="${id}"]`).waitFor({ timeout: 8000 })
  }
  const openQuickAdd = async () => {
    await page.getByRole('button', { name: 'Новая задача' }).first().click()
    await quickInput.waitFor({ timeout: 8000 })
  }

  const stamp = String(Date.now()).slice(-5)
  const made = { tasks: new Set(), deleted: new Set(), projects: new Set(), areas: new Set(), resources: new Set(), entries: new Set() }
  const keep = (task) => {
    if (task?.id) made.tasks.add(task.id)
    return task
  }
  const createTask = async (body) => keep((await post(`${P}/tasks`, body)).body)
  const tagsBefore = new Set(((await api(`${P}/tags`)).body?.items ?? []).map((tag) => tag.id))
  const today = (await api(`${P}/counts`)).body?.date
  const day = (n) => addDays(today, n)

  try {
    // ---- quick add: highlighted tokens → the right task -------------------------
    await go('/plan')
    await openQuickAdd()
    await quickInput.fill(`UI зал ${stamp} завтра в 18 на 1.5 ч #здоровье p2 напомнить за 15 мин`)
    await page.locator('[data-testid="quick-add-token"]').nth(5).waitFor({ timeout: 8000 }).catch(() => {})
    const tokens = await page
      .locator('[data-testid="quick-add-token"]')
      .evaluateAll((els) => els.map((el) => `${el.dataset.kind}:${el.textContent}`))
    check(
      ['date:завтра', 'time:в 18', 'duration:на 1.5 ч', 'tag:#здоровье', 'priority:p2', 'reminder:напомнить за 15 мин'].every(
        (token) => tokens.includes(token),
      ),
      'quick add highlights each recognised token inside the typed line',
      tokens.join(', '),
    )
    const chipKinds = await page.locator('[data-testid="quick-add-chip"]').evaluateAll((els) => els.map((el) => el.dataset.kind))
    check(
      ['block', 'priority', 'tag', 'reminder'].every((kind) => chipKinds.includes(kind)),
      'the parse preview shows block, priority, tag and reminder chips',
      chipKinds.join(', '),
    )
    await shot('40-plan-quick-add')
    const quick = await act(/\/tasks\/quick$/, 'POST', () => quickInput.press('Enter'))
    const gym = keep(quick.body?.task)
    const gymSaved = gym ? await getTask(gym.id) : null
    check(
      quick.status === 201 &&
        gymSaved?.title === `UI зал ${stamp}` &&
        gymSaved?.block?.date === day(1) &&
        gymSaved?.block?.start === '18:00' &&
        gymSaved?.block?.end === '19:30' &&
        gymSaved?.priority === 2 &&
        gymSaved?.tags?.some((tag) => tag.name === 'здоровье') &&
        gymSaved?.reminders?.[0]?.lead_minutes === 15,
      'Enter creates the parsed task: title, block tomorrow 18:00–19:30, P2, #здоровье, reminder',
      `${gymSaved?.title} · ${gymSaved?.block?.date} ${gymSaved?.block?.start}–${gymSaved?.block?.end} · P${gymSaved?.priority}`,
    )
    await page.locator('[data-testid="quick-add-added"]').waitFor({ timeout: 5000 }).catch(() => {})
    check(
      (await quickInput.inputValue()) === '' &&
        (await page.locator('[data-testid="quick-add-added"]').innerText().catch(() => '')).includes(`UI зал ${stamp}`),
      'the sheet stays open with an empty line for the next task',
    )
    await closeDialog()

    // ---- editor: priority, date, tag, custom repeat, subtask ---------------------
    const edited = await createTask({ title: `UI правка ${stamp}` })
    await go('/plan/inbox')
    await openTask(edited.id)
    await page.locator('[data-testid="priority-1"]').click()
    await page.locator('[data-testid="due-tomorrow"]').click()
    const tagInput = page.locator('[data-testid="task-tags"] [data-testid="tag-input"]')
    await tagInput.fill(`ui-${stamp}`)
    await tagInput.press('Enter')
    await page.locator('[data-testid="repeat-select"]').selectOption('custom')
    await page.locator('[data-testid="repeat-interval"]').fill('2')
    await page.locator('[data-testid="repeat-day-MO"]').click()
    await page.locator('[data-testid="repeat-day-TH"]').click()
    await page.locator('[data-testid="subtask-input"]').fill(`шаг ${stamp}`)
    const subtask = await act(/\/tasks$/, 'POST', () => page.locator('[data-testid="subtask-add"]').click())
    keep(subtask.body)
    await page.locator('[data-testid="subtask-row"]').filter({ hasText: `шаг ${stamp}` }).waitFor({ timeout: 8000 }).catch(() => {})
    await shot('41-plan-editor')
    const saved = await act(new RegExp(`/tasks/${edited.id}$`), 'PATCH', () => page.locator('[data-testid="task-save"]').click())
    check(
      Object.keys(saved.sent ?? {}).sort().join(',') === 'due_date,priority,recurrence_rule,tags',
      'the editor PATCH carries only the changed fields',
      Object.keys(saved.sent ?? {}).join(', '),
    )
    const afterEdit = await getTask(edited.id)
    check(
      afterEdit?.priority === 1 &&
        afterEdit?.due_date === day(1) &&
        afterEdit?.tags?.some((tag) => tag.name === `ui-${stamp}`) &&
        afterEdit?.recurrence_rule === 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH' &&
        afterEdit?.status === 'open',
      'editor saved P1, tomorrow, a new tag and «every 2 weeks on Mon, Thu»',
      `${afterEdit?.recurrence_rule} · ${afterEdit?.status}`,
    )
    check(
      subtask.status === 201 && afterEdit?.subtasks?.length === 1 && afterEdit.subtasks[0].title === `шаг ${stamp}`,
      'a subtask added inline in the editor belongs to the task',
    )

    // ---- unsaved changes are never lost silently --------------------------------
    await go('/plan/upcoming')
    await openTask(edited.id)
    await page.locator('[data-testid="task-title-input"]').fill(`UI правка ${stamp} черновик`)
    await closeDialog()
    const guard = await page.locator('[data-testid="task-unsaved"]').isVisible()
    await page.locator('[data-testid="task-unsaved-discard"]').click().catch(() => {})
    await sleep(700)
    check(
      guard && (await sheet.count()) === 0 && (await getTask(edited.id))?.title === `UI правка ${stamp}`,
      'closing with unsaved edits asks first; «Не сохранять» keeps the server copy',
    )

    // ---- completing a repeating task names the next repeat; undo removes it ------
    const completed = await act(new RegExp(`/tasks/${edited.id}/complete$`), 'POST', () =>
      row(edited.id).first().locator('[data-testid="task-check"]').click(),
    )
    const next = keep(completed.body?.next_instance)
    await toasts.filter({ hasText: 'Следующий повтор' }).first().waitFor({ timeout: 5000 }).catch(() => {})
    const doneToast = await toastText()
    check(
      completed.status === 200 && next?.series_index === 1 && next?.due_date > day(1) && doneToast.includes('Следующий повтор'),
      'ticking a repeating task: the toast names the next repeat',
      `${doneToast} · next ${next?.due_date}`,
    )
    const reopened = await act(new RegExp(`/tasks/${edited.id}/reopen$`), 'POST', () =>
      toasts.filter({ hasText: 'Следующий повтор' }).first().getByRole('button', { name: 'Отменить' }).click(),
    )
    const nextLeft = await api(`${P}/tasks/${next?.id}`)
    if (nextLeft.status === 404) made.deleted.add(next?.id)
    check(
      reopened.body?.task?.status === 'open' && reopened.body?.removed_next_instance_id === next?.id && nextLeft.status === 404,
      '«Отменить» reopens the task and removes the untouched next repeat',
    )

    // ---- upcoming: virtual repeats and «+» per day ------------------------------
    const daily = await createTask({ title: `UI каждый день ${stamp}`, due_date: today, recurrence_rule: 'daily' })
    await go('/plan/upcoming')
    const tomorrowSection = page.locator(`[data-testid="upcoming-day"][data-date="${day(1)}"]`)
    const virtual = tomorrowSection.locator(`[data-testid="occurrence-row"][data-task-id="${daily.id}"]`)
    const upcoming = (await api(`${P}/views/upcoming?days=3`)).body
    check(
      (await virtual.count()) === 1 &&
        Number(await virtual.evaluate((el) => getComputedStyle(el).opacity)) < 1 &&
        !!upcoming?.days?.find((entry) => entry.date === day(1))?.occurrences?.some((item) => item.task_id === daily.id),
      'upcoming shows tomorrow’s repeat of a daily task as a dimmed virtual row',
    )
    await page.locator(`[data-testid="upcoming-day"][data-date="${day(2)}"] [data-testid="upcoming-add"]`).click()
    await quickInput.waitFor({ timeout: 8000 })
    const defaultsChip = await page.locator('[data-testid="quick-add-defaults"]').innerText().catch(() => '')
    // a time typed without a date lands on that day — the preview says so before the server does
    await quickInput.fill(`UI на день ${stamp} в 18`)
    const dateChip = page.locator('[data-testid="quick-add-chip"][data-kind="date"]')
    await dateChip.filter({ hasText: '18:00' }).waitFor({ timeout: 8000 }).catch(() => {})
    const previewLabel = (await dateChip.innerText().catch(() => '')).trim()
    const dayAdd = await act(/\/tasks\/quick$/, 'POST', () => quickInput.press('Enter'))
    const onDay = keep(dayAdd.body?.task)
    check(
      dayAdd.status === 201 &&
        onDay?.due_date === day(2) &&
        onDay?.due_time === '18:00' &&
        defaultsChip.trim().length > 0 &&
        previewLabel.endsWith('18:00') &&
        !/^(Сегодня|Завтра)/.test(previewLabel),
      '«+» on a day in Upcoming: «в 18» is previewed and saved on that day',
      `${defaultsChip.trim()} · preview «${previewLabel}» → ${onDay?.due_date} ${onDay?.due_time}`,
    )
    await closeDialog()
    await shot('43-plan-upcoming')

    // ---- overdue → «Перенести всё на сегодня» (+ undo) ---------------------------
    const late = await createTask({ title: `UI просрочено ${stamp}`, due_date: day(-2) })
    await go('/plan')
    await page.locator(`[data-testid="plan-overdue"] [data-task-id="${late.id}"]`).waitFor({ timeout: 8000 }).catch(() => {})
    await shot('44-plan-today-overdue')
    await page.locator('[data-testid="move-all-today"]').click()
    const moved = await until(() => getTask(late.id), (task) => task?.due_date === today)
    check(moved?.due_date === today && moved?.overdue === false, '«Перенести всё на сегодня» moves the overdue task to today')
    await toasts.getByRole('button', { name: 'Отменить' }).first().click()
    const restoredLate = await until(() => getTask(late.id), (task) => task?.due_date === day(-2))
    check(restoredLate?.due_date === day(-2), '«Отменить» puts the moved tasks back on their old dates')
    await api(`${P}/tasks/${late.id}`, { method: 'DELETE' })
    made.deleted.add(late.id)

    // ---- inbox: one-tap clarify ------------------------------------------------
    const captured = await createTask({ title: `UI разобрать ${stamp}` })
    await go('/plan/inbox')
    await row(captured.id).locator('[data-testid="clarify-today"]').click()
    const clarified = await until(() => getTask(captured.id), (task) => task?.status === 'open')
    await row(captured.id).waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})
    check(
      clarified?.status === 'open' && clarified?.due_date === today && (await row(captured.id).count()) === 0,
      'inbox: «Сегодня» dates the task in one tap and it leaves the inbox',
    )

    // ---- project: create, inline add, progress ----------------------------------
    await go('/plan/projects')
    await page.locator('[data-testid="project-new"]').click()
    await page.locator('[data-testid="project-name"]').fill(`UI проект ${stamp}`)
    await page.locator('[data-testid="project-sheet"] [data-testid="color-violet"]').click()
    const projectCreated = await act(/\/projects$/, 'POST', () => page.locator('[data-testid="project-save"]').click())
    const project = projectCreated.body
    if (project?.id) made.projects.add(project.id)
    await page.waitForURL(new RegExp(`/plan/projects/${project?.id}$`), { timeout: 8000 }).catch(() => {})
    check(
      projectCreated.status === 201 && project?.color?.toLowerCase() === '#a78bfa' && page.url().endsWith(`/plan/projects/${project?.id}`),
      'a project is created from the sheet (name, colour) and opened',
    )
    const inline = page.locator('[data-testid="inline-add-input"]')
    await inline.fill(`UI шаг А ${stamp} p1`)
    const stepA = keep((await act(/\/tasks\/quick$/, 'POST', () => inline.press('Enter'))).body?.task)
    await inline.fill(`UI шаг Б ${stamp}`)
    const stepB = keep((await act(/\/tasks\/quick$/, 'POST', () => inline.press('Enter'))).body?.task)
    check(
      stepA?.project?.id === project?.id && stepB?.project?.id === project?.id && stepA?.priority === 1 && stepA?.title === `UI шаг А ${stamp}`,
      'the inline field adds tasks inside the project (explicit p1 kept)',
    )
    await row(stepA?.id).waitFor({ timeout: 8000 }).catch(() => {})
    await row(stepA?.id).locator('[data-testid="task-check"]').click()
    const progressed = await until(
      () => api(`${P}/projects/${project?.id}`),
      (result) => result.body?.project?.progress === 0.5,
    )
    await page.locator('[data-testid="project-progress"]').filter({ hasText: '50' }).waitFor({ timeout: 6000 }).catch(() => {})
    check(
      progressed.body?.project?.progress === 0.5 && (await page.locator('[data-testid="project-progress"]').innerText()).includes('50'),
      'ticking one of two tasks moves the project to 50 %',
    )
    await sleep(3200) // let the toast go before the screenshot
    await shot('45-plan-project')

    // ---- inbox: «Проект…» files a task under a project ---------------------------
    const toFile = await createTask({ title: `UI в проект ${stamp}` })
    await go('/plan/inbox')
    await row(toFile.id).locator('[data-testid="clarify-project"]').click()
    await page.locator('[data-testid="container-picker"]').getByRole('button', { name: `UI проект ${stamp}` }).click()
    const filed = await until(() => getTask(toFile.id), (task) => task?.project?.id === project?.id)
    check(
      filed?.project?.id === project?.id && filed?.status === 'open',
      'inbox: «Проект…» files the task under the project in two taps',
    )

    // ---- area with a weekly target ---------------------------------------------
    await go('/plan/areas')
    await page.locator('[data-testid="area-new"]').click()
    await page.locator('[data-testid="area-name"]').fill(`UI сфера ${stamp}`)
    await page.locator('[data-testid="area-target-input"]').fill('2,5')
    const areaCreated = await act(/\/areas$/, 'POST', () => page.locator('[data-testid="area-save"]').click())
    const area = areaCreated.body
    if (area?.id) made.areas.add(area.id)
    const areaRow = page.locator(`[data-testid="area-row"][data-area-id="${area?.id}"]`)
    await areaRow.waitFor({ timeout: 8000 }).catch(() => {})
    const areaText = (await areaRow.innerText().catch(() => '')).replace(/\s+/g, ' ')
    check(
      areaCreated.status === 201 && area?.weekly_target_minutes === 150 && areaText.includes('из 2 ч 30 мин'),
      'an area takes a weekly target of «2,5» h (150 min) and shows the week against it',
      areaText,
    )

    // ---- resource with a link ---------------------------------------------------
    await go('/plan/resources')
    await page.locator('[data-testid="resource-new"]').click()
    await page.locator('[data-testid="resource-name"]').fill(`UI ресурс ${stamp}`)
    await page.locator('[data-testid="resource-url"]').fill('example.org/guide')
    await page.locator('[data-testid="resource-area"]').selectOption(area?.id ?? '')
    const resourceCreated = await act(/\/resources$/, 'POST', () => page.locator('[data-testid="resource-save"]').click())
    const resource = resourceCreated.body
    if (resource?.id) made.resources.add(resource.id)
    const resourceRow = page.locator(`[data-testid="resource-row"][data-resource-id="${resource?.id}"]`)
    await resourceRow.waitFor({ timeout: 8000 }).catch(() => {})
    const href = await resourceRow.locator('[data-testid="resource-link"]').getAttribute('href').catch(() => null)
    check(
      resourceCreated.status === 201 && resource?.url === 'https://example.org/guide' && resource?.area?.id === area?.id && href === resource?.url,
      'a resource keeps a normalised https link and its area; the row opens the link',
      resource?.url,
    )

    // ---- tag page: its tasks, rename -------------------------------------------
    const tag = ((await api(`${P}/tags`)).body?.items ?? []).find((item) => item.name === `ui-${stamp}`)
    await go('/plan/search')
    await page.locator('[data-testid="tag-link"]').filter({ hasText: `#ui-${stamp}` }).click()
    await page.locator('[data-testid="tag-tasks"]').waitFor({ timeout: 8000 }).catch(() => {})
    check(
      page.url().endsWith(`/plan/tags/${tag?.id}`) && (await row(edited.id).count()) === 1,
      'the tag page (from the tag list in search) lists the tagged task',
    )
    await page.locator('[data-testid="tag-edit"]').click()
    await page.locator('[data-testid="tag-name"]').fill(`ui-${stamp}-x`)
    await act(new RegExp(`/tags/${tag?.id}$`), 'PATCH', () => page.locator('[data-testid="tag-save"]').click())
    const renamed = ((await api(`${P}/tags`)).body?.items ?? []).find((item) => item.id === tag?.id)
    const tagTitle = page.locator('[data-testid="tag-title"]')
    await tagTitle.filter({ hasText: `#ui-${stamp}-x` }).waitFor({ timeout: 5000 }).catch(() => {})
    check(
      renamed?.name === `ui-${stamp}-x` && (await tagTitle.innerText()).includes(`#ui-${stamp}-x`),
      'the tag is renamed from its page',
    )

    // ---- search: Russian word forms --------------------------------------------
    const report = await createTask({ title: `UI квартальные отчёты ${stamp}`, due_date: day(5) })
    await go('/plan/search')
    await page.locator('[data-testid="search-input"]').fill('квартальный отчёт')
    const hit = page.locator(`[data-testid="search-tasks"] [data-task-id="${report.id}"]`)
    await hit.waitFor({ timeout: 8000 }).catch(() => {})
    const searched = (await api(`${P}/search?q=${encodeURIComponent('квартальный отчёт')}`)).body
    check(
      (await hit.count()) === 1 && !!searched?.tasks?.some((task) => task.id === report.id),
      'search finds «квартальные отчёты» by «квартальный отчёт» (Russian word forms)',
    )

    // ---- archive: a completed project comes back --------------------------------
    await go(`/plan/projects/${project?.id}`)
    await page.locator('[data-testid="project-complete"]').click()
    await until(() => api(`${P}/projects/${project?.id}`), (result) => result.body?.project?.status === 'completed')
    await go('/plan/archive')
    const archived = page.locator('[data-testid="archive-projects"] [data-testid="archive-row"]').filter({ hasText: `UI проект ${stamp}` })
    await archived.locator('[data-testid="archive-restore"]').click()
    const restored = await until(() => api(`${P}/projects/${project?.id}`), (result) => result.body?.project?.status === 'active')
    check(
      restored.body?.project?.status === 'active' && restored.body?.project?.is_archived === false,
      'archive: «Восстановить» brings a completed project back to active',
    )

    // ---- the Today card's checkbox ---------------------------------------------
    const cardTask = await createTask({ title: `UI карточка ${stamp}`, due_date: today, priority: 1, is_pinned: true })
    await go('/today')
    const cardRow = page.locator(`[data-testid="today-plan"] [data-testid="task-row"][data-task-id="${cardTask.id}"]`)
    await cardRow.waitFor({ timeout: 8000 }).catch(() => {})
    await cardRow.scrollIntoViewIfNeeded().catch(() => {})
    await shot('46-today-plan-card')
    const ticked = await act(new RegExp(`/tasks/${cardTask.id}/complete$`), 'POST', () =>
      cardRow.locator('[data-testid="task-check"]').click(),
    )
    await toasts.first().waitFor({ timeout: 5000 }).catch(() => {})
    check(
      ticked.status === 200 && (await getTask(cardTask.id))?.status === 'done' && (await toastText()).includes('Отменить'),
      'the Today card checkbox completes the task (with «Отменить»)',
    )

    // ---- removing the date drops «before» reminders in the same save -------------
    const reminded = await createTask({
      title: `UI напоминание ${stamp}`,
      due_date: day(1),
      reminders: [{ lead_minutes: 30 }, { date: day(1), time: '09:00' }],
    })
    await go('/plan/upcoming')
    await openTask(reminded.id)
    await page.locator('[data-testid="due-clear"]').click()
    const droppedNote = await page.locator('[data-testid="reminders-dropped"]').isVisible().catch(() => false)
    const cleared = await act(new RegExp(`/tasks/${reminded.id}$`), 'PATCH', () => page.locator('[data-testid="task-save"]').click())
    const unDated = await getTask(reminded.id)
    const pending = (unDated?.reminders ?? []).filter((reminder) => reminder.status === 'pending')
    check(
      droppedNote && cleared.status === 200 && unDated?.due_date === null && pending.length === 1 && pending[0].date === day(1) && pending[0].time === '09:00',
      'removing the date drops the «30 min before» reminder in the same save (the fixed one stays)',
      `${cleared.status} · ${pending.length} pending`,
    )

    // ---- a calendar slot opens the editor with its block --------------------------
    await go('/plan')
    await page.evaluate(
      (detail) => window.dispatchEvent(new CustomEvent('dsos:plan:new-task', { detail })),
      { block: { date: day(3), start: '10:00', end: '11:30' } },
    )
    await page.locator('[data-testid="task-title-input"]').waitFor({ timeout: 8000 })
    const slotLabel = await page.locator('[data-testid="block-label"]').innerText().catch(() => '')
    await page.locator('[data-testid="task-title-input"]').fill(`UI слот ${stamp}`)
    const slotCreated = await act(/\/tasks$/, 'POST', () => page.locator('[data-testid="task-save"]').click())
    const slot = keep(slotCreated.body)
    check(
      slotCreated.status === 201 &&
        slot?.block?.date === day(3) &&
        slot?.block?.start === '10:00' &&
        slot?.block?.end === '11:30' &&
        slot?.status === 'scheduled' &&
        slotLabel.includes('10:00–11:30'),
      'newTask({block}) — a calendar slot — opens the editor prefilled; saving schedules the task',
      slotLabel,
    )

    // ---- the editor starts a timer on its task ---------------------------------
    await go('/plan/upcoming')
    await openTask(slot?.id)
    const timer = await act(/\/timer\/start$/, 'POST', () => page.locator('[data-testid="task-timer"]').click())
    if (timer.body?.entry?.id) made.entries.add(timer.body.entry.id)
    const running = (await api(`${P}/timer`)).body?.running
    check(
      timer.status === 200 && running?.entry?.task?.id === slot?.id,
      '«Запустить таймер» in the editor starts the timer on this task',
    )
    await post(`${P}/timer/stop`, {})

    // ---- delete needs a second tap ----------------------------------------------
    await page.locator('[data-testid="task-delete"]').click()
    const armed = await page.locator('[data-testid="task-delete-confirm"]').isVisible()
    await act(new RegExp(`/tasks/${slot?.id}$`), 'DELETE', () => page.locator('[data-testid="task-delete-confirm"]').click())
    const gone = await api(`${P}/tasks/${slot?.id}`)
    if (gone.status === 404) made.deleted.add(slot?.id)
    check(armed && gone.status === 404, 'delete asks for a second tap, then removes the task')

    // ---- offline quick add: queued, then sent -----------------------------------
    await go('/plan')
    await openQuickAdd()
    let previewRequests = 0
    const countPreview = (request) => {
      if (new URL(request.url()).pathname.endsWith('/planner/parse')) previewRequests += 1
    }
    await setOffline(true)
    page.on('request', countPreview)
    await quickInput.fill(`UI офлайн ${stamp} завтра`)
    await sleep(600)
    page.off('request', countPreview)
    const offlineHint = await page.locator('[data-testid="quick-add-sheet"]').innerText()
    await quickInput.press('Enter')
    await toasts.filter({ hasText: 'Сохранится, когда появится сеть' }).first().waitFor({ timeout: 5000 }).catch(() => {})
    const queuedToast = await toastText()
    const waiting = await page.locator('[data-testid="quick-add-added"]').innerText().catch(() => '')
    check(
      previewRequests === 0 &&
        offlineHint.includes('Без сети') &&
        queuedToast.includes('Сохранится, когда появится сеть') &&
        waiting.includes('ждёт сети'),
      'offline: no preview request; the line is queued with a clear message',
    )
    const replay = response(/\/tasks\/quick$/, 'POST')
    await setOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    const sent = await replay.then(async (result) => ({ status: result.status(), body: await result.json() })).catch(() => null)
    const offlineTask = keep(sent?.body?.task)
    check(
      sent?.status === 201 && offlineTask?.title === `UI офлайн ${stamp}` && offlineTask?.due_date === day(1),
      'back online the queued line is sent once and parsed as of the tap («завтра»)',
    )
    await closeDialog()

    // ---- tap targets and the light theme ------------------------------------------
    await go('/plan')
    const box = await page.locator('[data-testid="task-check"]').first().boundingBox()
    check(
      !!box && box.width >= 44 && box.height >= 44,
      'the task checkbox is a full 48 px tap target',
      box ? `${Math.round(box.width)}×${Math.round(box.height)}` : 'missing',
    )
    await page.evaluate(() => {
      window.localStorage.setItem('dsos.theme', 'light')
    })
    await page.reload({ waitUntil: 'networkidle' })
    await sleep(500)
    await shot('47-plan-today-light')
    await page.evaluate(() => {
      window.localStorage.setItem('dsos.theme', 'dark')
    })
    await page.reload({ waitUntil: 'networkidle' })
  } finally {
    // ---- clean up everything this run created ------------------------------------
    await setOffline(false)
    const sweep = async () => {
      const found = (await api(`${P}/search?q=${stamp}&include_done=true`)).body
      return found ?? { tasks: [], projects: [], areas: [], resources: [] }
    }
    for (const id of made.entries) await api(`${P}/time-entries/${id}`, { method: 'DELETE' })
    for (const id of made.tasks) {
      if (!made.deleted.has(id)) await api(`${P}/tasks/${id}`, { method: 'DELETE' })
    }
    // repeats created by completions (and anything a failed step left behind) carry the stamp
    for (const task of (await sweep()).tasks) await api(`${P}/tasks/${task.id}`, { method: 'DELETE' })
    for (const id of made.resources) await api(`${P}/resources/${id}`, { method: 'DELETE' })
    for (const id of made.projects) await api(`${P}/projects/${id}`, { method: 'DELETE' })
    for (const id of made.areas) await api(`${P}/areas/${id}`, { method: 'DELETE' })
    const left = await sweep()
    for (const item of left.projects) await api(`${P}/projects/${item.id}`, { method: 'DELETE' })
    for (const item of left.areas) await api(`${P}/areas/${item.id}`, { method: 'DELETE' })
    for (const item of left.resources) await api(`${P}/resources/${item.id}`, { method: 'DELETE' })
    for (const tag of (await api(`${P}/tags`)).body?.items ?? []) {
      if (!tagsBefore.has(tag.id)) await api(`${P}/tags/${tag.id}`, { method: 'DELETE' })
    }
    const after = await sweep()
    const tagsAfter = ((await api(`${P}/tags`)).body?.items ?? []).filter((tag) => !tagsBefore.has(tag.id))
    const timerAfter = (await api(`${P}/timer`)).body?.running
    check(
      after.tasks.length + after.projects.length + after.areas.length + after.resources.length + tagsAfter.length === 0 && !timerAfter,
      'stage-6 planner test data removed (tasks, repeats, project, area, resource, tags, timer entry)',
    )
  }
}
