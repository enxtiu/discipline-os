/**
 * Browser checks — stage 9 accessibility. Called by scripts/ui-check.mjs last.
 *
 *  - axe-core (a pinned devDependency, injected into the page — never bundled) with the
 *    WCAG 2.1 A/AA tags on every main screen and sheet, in the dark and the light theme.
 *    Any serious or critical violation fails and is listed with its selector; moderate
 *    and minor ones are reported. The full result goes to $SHOTS/a11y-axe.json.
 *  - Touch targets: axe does not measure them, so every button, link and control of those
 *    screens is measured against the project's 48 px rule (both sides); the tab bar's
 *    targets must also not be covered by the floating quick-add button.
 *  - Keyboard: Tab through Today (focus always visible, never hidden under the fixed bars),
 *    a sheet and the task editor (focus moves in, stays trapped, Escape closes, focus
 *    returns to the opener).
 *  - Live regions (a toast is announced politely and does not take focus), reduced motion
 *    (CSS and framer-motion), <html lang> follows the UI language.
 *
 * Screens that only exist on the integrated stage-9 branch (the sync sheet behind the
 * header pill, Settings → Интеграции) are visited when present and skipped with a note
 * otherwise. Everything this module creates is removed at the end.
 */
import { randomUUID } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
const BLOCKING = new Set(['serious', 'critical'])
const MIN_TARGET = 48
const THEMES = ['dark', 'light']

/**
 * Targets that may stay under 48 px, each with the reason. All of them still meet WCAG 2.5.8
 * (AA: 24 × 24 px or spaced 24 px apart); the project's 48 px rule yields where the size of
 * the target carries the data (a day column of a chart, a time block as tall as its duration)
 * and the same thing can be done from a full-size control elsewhere.
 * Matched with `Element.closest()`; inline links inside a sentence are excepted in code.
 */
const TARGET_EXCEPTIONS = [
  {
    selector: '[data-testid="burned-chart"] li > button, [data-testid="time-summary"] li > button',
    reason: 'chart bar: 7 days (or a month) side by side on a 390 px card — ≥ 24 px wide and 100+ px tall; its value is its spoken name and shows on tap',
  },
  {
    selector: '[data-testid="steps-chart"] [role="listitem"] > button',
    reason: 'chart bar: 14 days side by side (24 px wide, 130 px tall); its value is its spoken name and shows on tap',
  },
  {
    selector: '[data-testid="cal-day-header"]',
    reason: 'week grid column header: 7 columns beside the hour gutter (45 px wide, 48 px tall); the day view arrows reach the same days',
  },
  {
    selector: '[data-testid="cal-grid"][data-view="week"] [data-testid="cal-allday-task"], [data-testid="cal-grid"][data-view="week"] [data-testid="cal-occurrence"]',
    reason: 'compact chip in a week column; the day view shows the same task as a 48 px chip',
  },
  {
    selector: '[data-testid="cal-block"], [data-testid="cal-continuation"], [data-testid="cal-layer"] [data-testid="cal-occurrence"], [data-testid="cal-resize"]',
    reason: 'time block: as tall as its duration (essential); the same task opens from the day list and Plan › Сегодня at 48 px',
  },
]

function loadAxe() {
  try {
    const require = createRequire(new URL('../../web/package.json', import.meta.url))
    const version = JSON.parse(readFileSync(require.resolve('axe-core/package.json'), 'utf8')).version
    return { source: readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8'), version }
  } catch {
    return null
  }
}

/** In the page: a short, readable selector for an element. */
const describeElementSource = `
  window.__a11yDescribe = (el) => {
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
  window.__a11yName = (el) =>
    (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 50)
`

export async function run(ctx) {
  const { page, context, WEB_URL, SHOTS, check, ok, bad, shot } = ctx
  console.log('\n9a. stage 9 accessibility — axe-core, 48 px targets, keyboard, live regions')

  const axe = loadAxe()
  if (!axe) {
    bad('axe-core is installed (web/node_modules/axe-core)', 'run `npm install` in web/')
    return
  }
  ok('axe-core loaded for the browser checks', `v${axe.version}, tags ${AXE_TAGS.join(' ')}`)

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

  // ------------------------------------------------------------------------ //
  // helpers
  // ------------------------------------------------------------------------ //
  const report = { axe: axe.version, tags: AXE_TAGS, states: [], targets: [], keyboard: [], skipped: [] }
  const totals = { blocking: 0, moderate: 0, minor: 0, statesRun: 0 }

  const setTheme = (theme, target = page) =>
    target.evaluate((value) => {
      const root = document.documentElement
      root.dataset.theme = value
      root.classList.toggle('dark', value === 'dark')
      window.localStorage.setItem('dsos.theme', value)
    }, theme)

  /** Wait until finite animations and transitions are done (spinners/shimmer loop forever). */
  const settle = async (ms = 350, target = page) => {
    await target.waitForTimeout(ms)
    await target
      .evaluate(async () => {
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

  const inject = async (target = page) => {
    const missing = await target.evaluate(() => typeof window.axe === 'undefined' || typeof window.__a11yDescribe === 'undefined')
    if (missing) {
      await target.addScriptTag({ content: axe.source })
      await target.addScriptTag({ content: describeElementSource })
    }
  }

  /** axe on the whole document, or on the topmost open dialog. */
  const runAxe = async (scope, target = page) => {
    await inject(target)
    return target.evaluate(
      async ({ scope, tags }) => {
        let root = document
        if (scope === 'dialog') {
          const dialogs = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
          root = dialogs[dialogs.length - 1] ?? document
        }
        const result = await window.axe.run(root, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] })
        return result.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          help: violation.help,
          nodes: violation.nodes.map((node) => ({
            target: node.target.join(' '),
            html: node.html.replace(/\s+/g, ' ').slice(0, 180),
            summary: (node.failureSummary ?? '').replace(/\s+/g, ' ').slice(0, 300),
          })),
        }))
      },
      { scope, tags: AXE_TAGS },
    )
  }

  /** Every operable control in scope under 48 × 48 px (inline text links and listed exceptions aside). */
  const measureTargets = async (scope, target = page) => {
    await inject(target)
    return target.evaluate(
      ({ scope, min, exceptions }) => {
        const dialogs = [...document.querySelectorAll('[role="dialog"]')]
        const root = scope === 'dialog' ? (dialogs[dialogs.length - 1] ?? document.body) : document.body
        const selector = [
          'a[href]',
          'button',
          'input:not([type="hidden"])',
          'select',
          'textarea',
          'summary',
          '[role="button"]',
          '[role="link"]',
          '[role="tab"]',
          '[role="switch"]',
          '[role="checkbox"]',
          '[role="radio"]',
          '[role="menuitem"]',
          '[role="option"]',
          '[tabindex]:not([tabindex="-1"])',
        ].join(',')
        const small = []
        const excepted = []
        for (const el of root.querySelectorAll(selector)) {
          if (scope !== 'dialog' && dialogs.some((dialog) => dialog.contains(el))) continue
          if (el.closest('[aria-hidden="true"], [inert]') || el.disabled) continue
          const style = getComputedStyle(el)
          if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') continue
          const rect = el.getBoundingClientRect()
          if (rect.width <= 2 || rect.height <= 2) continue // visually hidden (sr-only inputs)
          // the fixed bars: a target there must not sit under another one (the floating «+»)
          if (el.closest('nav.tabbar, header.sticky')) {
            const x = rect.left + rect.width / 2
            const y = rect.top + rect.height / 2
            const hit = x >= 0 && y >= 0 && x <= window.innerWidth && y <= window.innerHeight ? document.elementFromPoint(x, y) : null
            if (hit && !(hit === el || el.contains(hit))) {
              small.push({
                selector: window.__a11yDescribe(el),
                name: window.__a11yName(el),
                size: `${Math.round(rect.width)}×${Math.round(rect.height)} covered by ${window.__a11yDescribe(hit)}`,
              })
              continue
            }
          }
          if (rect.width + 0.5 >= min && rect.height + 0.5 >= min) continue
          // a checkbox/radio inside a big enough <label>: the label is the target
          const label = el.matches('input') ? el.closest('label') : null
          if (label) {
            const box = label.getBoundingClientRect()
            if (box.width + 0.5 >= min && box.height + 0.5 >= min) continue
          }
          const entry = {
            selector: window.__a11yDescribe(el),
            name: window.__a11yName(el),
            size: `${Math.round(rect.width)}×${Math.round(rect.height)}`,
          }
          // WCAG 2.5.8 "inline": a link inside a sentence is sized by the line of text
          const parentText = (el.parentElement?.textContent ?? '').trim().length
          if (el.tagName === 'A' && style.display === 'inline' && parentText > (el.textContent ?? '').trim().length + 3) {
            excepted.push({ ...entry, reason: 'inline link in a sentence' })
            continue
          }
          const rule = exceptions.find((exception) => el.closest(exception.selector))
          if (rule) {
            excepted.push({ ...entry, reason: rule.reason })
            continue
          }
          small.push(entry)
        }
        // a control whose role is overridden by a non-interactive one (`<button role="listitem">`)
        // is announced as that role: VoiceOver never says it can be pressed
        const overridden = []
        for (const el of root.querySelectorAll('button[role], a[href][role], input[role], select[role], textarea[role]')) {
          if (scope !== 'dialog' && dialogs.some((dialog) => dialog.contains(el))) continue
          const role = el.getAttribute('role')
          if (!/^(button|link|tab|switch|checkbox|radio|menuitem|menuitemcheckbox|menuitemradio|option|combobox|searchbox|textbox|slider|spinbutton)$/.test(role)) {
            overridden.push({ selector: window.__a11yDescribe(el), name: window.__a11yName(el), role })
          }
        }
        // a chart or other drawing (an svg with several shapes) is either hidden from screen
        // readers (decoration, icons) or inside a named role="img" — its text alternative
        const unnamedGraphics = []
        for (const svg of root.querySelectorAll('svg')) {
          if (scope !== 'dialog' && dialogs.some((dialog) => dialog.contains(svg))) continue
          if (svg.closest('[aria-hidden="true"]')) continue
          if (svg.querySelectorAll('path, rect, circle, line, polyline, polygon, ellipse').length < 3) continue
          const box = svg.getBoundingClientRect()
          if (box.width < 2 || box.height < 2) continue
          const named = svg.closest('[role="img"][aria-label], [role="img"][aria-labelledby]')
          if (!named || !(named.getAttribute('aria-label') || named.getAttribute('aria-labelledby'))) {
            unnamedGraphics.push({ selector: window.__a11yDescribe(svg), name: `${Math.round(box.width)}×${Math.round(box.height)}` })
          }
        }
        // pinch-zoom is allowed, so iOS Safari zooms into any text field under 16 px on focus
        const tinyFields = []
        for (const el of root.querySelectorAll('input, textarea, select')) {
          if (scope !== 'dialog' && dialogs.some((dialog) => dialog.contains(el))) continue
          if (/^(checkbox|radio|range|file|hidden|color|submit|button)$/.test(el.type)) continue
          const rect = el.getBoundingClientRect()
          if (rect.width <= 2 || rect.height <= 2) continue
          const size = parseFloat(getComputedStyle(el).fontSize)
          if (size < 16) tinyFields.push({ selector: window.__a11yDescribe(el), name: window.__a11yName(el), size: `${size}px` })
        }
        return { small, excepted, tinyFields, overridden, unnamedGraphics }
      },
      { scope, min: MIN_TARGET, exceptions: TARGET_EXCEPTIONS },
    )
  }

  const formatNodes = (violations) =>
    violations.flatMap((violation) =>
      violation.nodes.map((node) => `${violation.id} [${violation.impact}] ${node.target}`),
    )

  /**
   * One screen or sheet: `open()` brings it up (in the dark theme), then axe runs in dark
   * and light; targets are measured once (theme does not change sizes).
   */
  const audit = async (name, open, { scope = null, optional = false, targets = true, screenshot = null } = {}) => {
    try {
      await setTheme('dark')
      const present = await open()
      if (present === false) {
        if (optional) {
          report.skipped.push(name)
          ok(`a11y ${name}: not on this branch — skipped`)
        } else {
          bad(`a11y ${name}: could not open the screen`)
        }
        return
      }
      await settle()
      if (targets) {
        const measured = await measureTargets(scope)
        report.targets.push({ state: name, ...measured })
        check(
          measured.small.length === 0,
          `a11y ${name}: every target is at least 48 × 48 px`,
          measured.small.length
            ? `${measured.small.length} small: ` + measured.small.slice(0, 6).map((entry) => `${entry.selector} «${entry.name}» ${entry.size}`).join(' | ')
            : `${measured.excepted.length} excepted`,
        )
        for (const entry of measured.small) console.log(`         small target ${entry.size} ${entry.selector} «${entry.name}»`)
        if (measured.tinyFields.length) {
          bad(
            `a11y ${name}: text fields are at least 16 px (no zoom on focus)`,
            measured.tinyFields.slice(0, 4).map((entry) => `${entry.selector} ${entry.size}`).join(' | '),
          )
        }
        if (measured.unnamedGraphics.length) {
          bad(
            `a11y ${name}: every chart or drawing has a text alternative`,
            measured.unnamedGraphics.slice(0, 4).map((entry) => `${entry.selector} ${entry.name}`).join(' | '),
          )
        }
        if (measured.overridden.length) {
          bad(
            `a11y ${name}: controls keep their role (no <button role="listitem">)`,
            measured.overridden.slice(0, 4).map((entry) => `${entry.selector} role=${entry.role}`).join(' | '),
          )
        }
      }
      for (const theme of THEMES) {
        if (theme !== 'dark') {
          await setTheme(theme)
          await settle(300)
        }
        const violations = await runAxe(scope)
        totals.statesRun += 1
        const blocking = violations.filter((violation) => BLOCKING.has(violation.impact))
        const moderate = violations.filter((violation) => !BLOCKING.has(violation.impact))
        totals.blocking += blocking.reduce((count, violation) => count + violation.nodes.length, 0)
        for (const violation of moderate) {
          totals[violation.impact === 'moderate' ? 'moderate' : 'minor'] += violation.nodes.length
        }
        report.states.push({ state: name, theme, violations })
        const lines = formatNodes(blocking)
        check(
          blocking.length === 0,
          `axe ${name} (${theme}): no serious/critical violations`,
          lines.length ? `${lines.length} node(s): ${lines.slice(0, 4).join(' | ')}` : `${moderate.length} moderate/minor rule(s)`,
        )
        for (const line of lines) console.log(`         ${line}`)
        for (const line of formatNodes(moderate)) console.log(`         note ${line}`)
        if (screenshot && theme === 'light') await shot(`${screenshot}-light`)
        if (screenshot && theme === 'dark') await shot(`${screenshot}-dark`)
      }
    } catch (error) {
      bad(`a11y ${name}`, String(error?.message ?? error).split('\n')[0])
    } finally {
      await setTheme('dark').catch(() => {})
    }
  }

  /** Unseen achievements open a celebration sheet on Today and the achievements screen. */
  const unseen = async () => (await apiFetch('/v1/gamification/today')).body?.celebrate ?? []
  const markSeen = async () => {
    const items = await unseen()
    if (items.length) {
      await apiFetch('/v1/gamification/achievements/seen', { method: 'POST', body: JSON.stringify({ ids: items.map((item) => item.id) }) })
    }
  }
  const goto = async (path, ready) => {
    if (/^\/(today|stats)/.test(path)) await markSeen().catch(() => {})
    await page.goto(`${WEB_URL}${path}`, { waitUntil: 'networkidle' })
    if (ready) await page.locator(ready).first().waitFor({ timeout: 15_000 })
    // skeletons gone
    await page
      .waitForFunction(() => !document.querySelector('main .skeleton'), null, { timeout: 10_000 })
      .catch(() => {})
  }
  const topDialog = () => page.locator('[role="dialog"]').last()
  const waitDialog = async () => {
    await topDialog().waitFor({ timeout: 10_000 })
    return true
  }
  const openQuickAdd = async () => {
    await page.locator('nav.tabbar button[aria-label]').first().click()
    return waitDialog()
  }

  // ------------------------------------------------------------------------ //
  // seed: a day with data (everything is removed at the end)
  // ------------------------------------------------------------------------ //
  const created = { meals: [], water: [], steps: null, sleep: null, workouts: [], tasks: [], projects: [], areas: [], resources: [], entries: [], timer: null }
  let T = null
  let liveWorkout = null
  let editTask = null
  let project = null

  const errors = []
  const onConsole = (message) => {
    if (message.type() === 'error' && !/favicon|404 \(Not Found\)|Failed to load resource/i.test(message.text())) errors.push(message.text())
  }
  const onPageError = (error) => errors.push(String(error))
  page.on('console', onConsole)
  page.on('pageerror', onPageError)

  try {
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    const statuses = []
    const day = (await apiFetch('/v1/nutrition/day')).body
    T = day?.date
    const tomorrow = new Date(Date.parse(`${T}T12:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
    const yesterday = new Date(Date.parse(`${T}T12:00:00Z`) - 86_400_000).toISOString().slice(0, 10)
    const remember = (response, list, id) => {
      statuses.push(response.status)
      if (response.status === 200 || response.status === 201) list.push(id ?? response.body?.id)
      return response
    }
    const meal = await apiFetch('/v1/nutrition/meals', {
      method: 'POST',
      body: JSON.stringify({
        id: randomUUID(),
        meal_type: 'breakfast',
        source: 'manual',
        items: [{ name: 'Овсянка (a11y ui-check)', grams: 250, kcal_per_100g: 88, protein_per_100g: 3, fat_per_100g: 1.7, carbs_per_100g: 15, fiber_per_100g: 2 }],
      }),
    })
    remember(meal, created.meals)
    remember(await apiFetch('/v1/nutrition/water', { method: 'POST', body: JSON.stringify({ id: randomUUID(), amount_ml: 500 }) }), created.water)
    const health = (await apiFetch('/v1/health/today')).body
    if (health?.day?.steps == null) {
      const steps = await apiFetch(`/v1/health/steps/${T}`, { method: 'PUT', body: JSON.stringify({ steps: 6400 }) })
      statuses.push(steps.status)
      if (steps.status === 200) created.steps = T
    }
    if (!health?.sleep) {
      const sleep = await apiFetch(`/v1/health/sleep/${T}`, { method: 'PUT', body: JSON.stringify({ bed_time: '23:40', wake_time: '07:10' }) })
      statuses.push(sleep.status)
      if (sleep.status === 200) created.sleep = T
    }
    // a finished workout today and a live one for the workout screen
    const exercises = (await apiFetch(`/v1/training/exercises?q=${encodeURIComponent('жим лёжа')}&limit=5`)).body
    const bench = (exercises?.items ?? exercises ?? [])[0]?.id
    const leftover = (await apiFetch('/v1/training/today')).body?.active
    if (leftover) await apiFetch(`/v1/training/workouts/${leftover.id}`, { method: 'DELETE' })
    const done = randomUUID()
    remember(await apiFetch('/v1/training/workouts', { method: 'POST', body: JSON.stringify({ id: done, kind: 'strength', title: 'A11y силовая (ui-check)' }) }), created.workouts, done)
    for (const index of [1, 2]) {
      statuses.push(
        (
          await apiFetch(`/v1/training/workouts/${done}/sets/${randomUUID()}`, {
            method: 'PUT',
            body: JSON.stringify({ exercise_id: bench, sort_order: 0, set_index: index, is_warmup: false, reps: 8, weight_kg: 60 }),
          })
        ).status,
      )
    }
    statuses.push((await apiFetch(`/v1/training/workouts/${done}/finish`, { method: 'POST', body: JSON.stringify({ duration_s: 1800 }) })).status)
    liveWorkout = randomUUID()
    remember(await apiFetch('/v1/training/workouts', { method: 'POST', body: JSON.stringify({ id: liveWorkout, kind: 'strength', title: 'A11y живая (ui-check)' }) }), created.workouts, liveWorkout)
    statuses.push(
      (
        await apiFetch(`/v1/training/workouts/${liveWorkout}/sets/${randomUUID()}`, {
          method: 'PUT',
          body: JSON.stringify({ exercise_id: bench, sort_order: 0, set_index: 1, is_warmup: false, reps: 10, weight_kg: 50 }),
        })
      ).status,
    )
    // planner: an area, a project, a resource, tasks for today / tomorrow / the inbox, time
    const areaId = randomUUID()
    remember(await apiFetch('/v1/planner/areas', { method: 'POST', body: JSON.stringify({ id: areaId, name: 'A11y сфера (ui-check)', weekly_target_minutes: 120 }) }), created.areas, areaId)
    project = randomUUID()
    remember(
      await apiFetch('/v1/planner/projects', { method: 'POST', body: JSON.stringify({ id: project, name: 'A11y проект (ui-check)', area_id: areaId, color: '#8B5CF6' }) }),
      created.projects,
      project,
    )
    const resourceId = randomUUID()
    remember(
      await apiFetch('/v1/planner/resources', { method: 'POST', body: JSON.stringify({ id: resourceId, name: 'A11y справка (ui-check)', url: 'https://example.org/a11y', area_id: areaId }) }),
      created.resources,
      resourceId,
    )
    editTask = randomUUID()
    remember(
      await apiFetch('/v1/planner/tasks', {
        method: 'POST',
        body: JSON.stringify({
          id: editTask,
          title: 'A11y отчёт (ui-check)',
          priority: 2,
          project_id: project,
          due_date: T,
          block: { date: T, start: '10:00', end: '11:00' },
          tags: ['a11y'],
          notes_md: 'Проверить **контраст** и фокус.',
          reminders: [{ lead_minutes: 15 }],
        }),
      }),
      created.tasks,
      editTask,
    )
    const tomorrowTask = randomUUID()
    remember(await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ id: tomorrowTask, title: 'A11y завтра (ui-check)', due_date: tomorrow, priority: 3 }) }), created.tasks, tomorrowTask)
    const inboxTask = randomUUID()
    remember(await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ id: inboxTask, title: 'A11y идея (ui-check)' }) }), created.tasks, inboxTask)
    // a finished task (dimmed in lists and the calendar) and a daily repeat (future occurrences)
    const doneTask = randomUUID()
    remember(
      await apiFetch('/v1/planner/tasks', {
        method: 'POST',
        body: JSON.stringify({ id: doneTask, title: 'A11y готово (ui-check)', due_date: T, block: { date: T, start: '07:00', end: '07:30' }, project_id: project }),
      }),
      created.tasks,
      doneTask,
    )
    statuses.push((await apiFetch(`/v1/planner/tasks/${doneTask}/complete`, { method: 'POST', body: '{}' })).status)
    const repeatTask = randomUUID()
    remember(
      await apiFetch('/v1/planner/tasks', {
        method: 'POST',
        body: JSON.stringify({
          id: repeatTask,
          title: 'A11y зарядка (ui-check)',
          due_date: T,
          block: { date: T, start: '18:00', end: '18:30' },
          recurrence_rule: 'FREQ=DAILY',
          priority: 3,
        }),
      }),
      created.tasks,
      repeatTask,
    )
    const entryId = randomUUID()
    remember(
      await apiFetch('/v1/planner/time-entries', { method: 'POST', body: JSON.stringify({ id: entryId, area_id: areaId, date: yesterday, start: '08:00', end: '09:15', description: 'A11y (ui-check)' }) }),
      created.entries,
      entryId,
    )
    const timerId = randomUUID()
    const timer = await apiFetch('/v1/planner/timer/start', { method: 'POST', body: JSON.stringify({ id: timerId, project_id: project, description: 'A11y таймер (ui-check)' }) })
    statuses.push(timer.status)
    if (timer.status === 200 || timer.status === 201) created.timer = timerId
    check(
      statuses.every((status) => status === 200 || status === 201) && !!T,
      'seeded a day for the audit: meal, water, steps, sleep, workouts, area, project, resource, tasks, time, a running timer',
      `${statuses.length} writes on ${T}`,
    )

    // ---------------------------------------------------------------------- //
    // 1. axe + targets on every screen and sheet, dark and light
    // ---------------------------------------------------------------------- //
    await audit(
      'celebration sheet',
      async () => {
        if (!(await unseen()).length) return false
        await page.goto(`${WEB_URL}/stats/achievements`, { waitUntil: 'networkidle' })
        return topDialog()
          .waitFor({ timeout: 8000 })
          .then(() => true)
          .catch(() => false)
      },
      { scope: 'dialog', optional: true },
    )
    await markSeen()
    await audit('Today (with data)', async () => goto('/today', '[data-testid="burned-today"]'), { screenshot: '90-a11y-today' })
    await audit('quick-add sheet', async () => {
      await goto('/today', '[data-testid="burned-today"]')
      return openQuickAdd()
    }, { scope: 'dialog' })
    await audit('score sheet', async () => {
      await goto('/today', '[data-testid="burned-today"]')
      await page.locator('[data-testid="day-score-button"]').click()
      return waitDialog()
    }, { scope: 'dialog' })
    await audit('steps sheet', async () => {
      await goto('/today?action=steps', '[data-testid="burned-today"]')
      return waitDialog()
    }, { scope: 'dialog' })
    await audit('Food', async () => goto('/food', 'input[placeholder*="Поиск"]'))
    await audit('recipe sheet', async () => {
      await goto('/food?action=recipe', 'main')
      await page.locator('#recipe-name').waitFor({ timeout: 8000 })
      return true
    }, { scope: 'dialog' })
    await audit('log sheet', async () => {
      await goto('/food', 'input[placeholder*="Поиск"]')
      await page.locator('input[placeholder*="Поиск"]').first().fill('гречка отварная')
      await page.locator('section div.surface-sunken > button').first().waitFor({ timeout: 10_000 })
      await page.locator('section div.surface-sunken > button').first().click()
      return waitDialog()
    }, { scope: 'dialog' })
    await audit('AI review', async () => {
      await goto('/today', '[data-testid="burned-today"]')
      await openQuickAdd()
      await topDialog().getByText('Текст', { exact: true }).click()
      await page.locator('#ai-text').fill('200 г гречки и 2 яйца')
      await page.getByRole('button', { name: 'Распознать' }).click()
      await page.locator('[data-testid="ai-item"]').first().waitFor({ timeout: 15_000 })
      return true
    }, { scope: 'dialog' })
    await audit('Train', async () => goto('/train', '[data-testid="week-strip"]'))
    await audit('template editor', async () => {
      await goto('/train', '[data-testid="week-strip"]')
      await page.getByRole('button', { name: 'Новый шаблон' }).first().click()
      await page.locator('#template-title').waitFor({ timeout: 8000 })
      return true
    }, { scope: 'dialog' })
    await audit('live workout', async () => goto(`/train/workout/${liveWorkout}`, '[data-testid="workout-exercise"]'))
    await audit('Health', async () => goto('/health', '[data-testid="steps-chart"]'))
    await audit('Plan · today', async () => goto('/plan', '[data-testid="plan-today"]'))
    await audit('Plan · inbox', async () => goto('/plan/inbox', '[data-testid="inbox"]'))
    await audit('Plan · upcoming', async () => goto('/plan/upcoming', '[data-testid="upcoming"]'))
    await audit('calendar · day', async () => goto(`/plan/calendar?view=day&date=${T}`, '[data-testid="cal-grid"][data-view="day"]'))
    await audit('calendar · week', async () => goto(`/plan/calendar?view=week&date=${T}`, '[data-testid="cal-grid"][data-view="week"]'))
    await audit('calendar · month', async () => goto(`/plan/calendar?view=month&date=${T}`, '[data-testid="cal-month"]'))
    await audit('task editor', async () => {
      await goto('/plan', '[data-testid="plan-today"]')
      await page.locator(`[data-testid="task-row"][data-task-id="${editTask}"] [data-testid="task-title"]`).first().click()
      await page.locator(`[data-testid="task-sheet"][data-task-id="${editTask}"] [data-testid="task-title-input"]`).waitFor({ timeout: 10_000 })
      return true
    }, { scope: 'dialog' })
    await audit('task quick add', async () => {
      await goto('/plan', '[data-testid="plan-today"]')
      await page.getByRole('button', { name: 'Новая задача' }).first().click()
      await page.locator('[data-testid="quick-add-input"]').waitFor({ timeout: 8000 })
      await page.locator('[data-testid="quick-add-input"]').fill('позвонить завтра в 15:00 #a11y !2')
      await page.locator('[data-testid="quick-add-chip"]').first().waitFor({ timeout: 8000 }).catch(() => {})
      return true
    }, { scope: 'dialog' })
    await audit('projects', async () => goto('/plan/projects', '[data-testid="projects-page"]'))
    await audit('project page', async () => goto(`/plan/projects/${project}`, '[data-testid="project-progress"]'))
    await audit('project sheet', async () => {
      await goto('/plan/projects', '[data-testid="projects-page"]')
      await page.locator('[data-testid="project-new"]').click()
      await page.locator('[data-testid="project-name"]').waitFor({ timeout: 8000 })
      return true
    }, { scope: 'dialog' })
    await audit('areas', async () => goto('/plan/areas', '[data-testid="areas-page"]'))
    await audit('resources', async () => goto('/plan/resources', '[data-testid="resources-page"]'))
    await audit('journal', async () => goto('/plan/journal', '[data-testid="journal-page"]'))
    await audit('time log', async () => goto('/plan/time', '[data-testid="time-page"]'))
    await audit('Stats dashboards', async () => {
      await goto('/stats', '[data-testid="stats-page"]')
      await page
        .waitForFunction(
          () =>
            !document.querySelector('[data-testid="stats-page"][data-loading]') &&
            !document.querySelector('[data-testid^="stats-skeleton-"]') &&
            document.querySelectorAll('[data-testid^="stats-section-"]').length === 5,
          null,
          { timeout: 20_000 },
        )
        .catch(() => {})
      return true
    }, { screenshot: '91-a11y-stats' })
    await audit('achievements', async () => goto('/stats/achievements', '[data-testid="achievements-page"]'))
    await audit('achievement sheet', async () => {
      await goto('/stats/achievements', '[data-testid="achievements-page"]')
      await page.locator('[data-testid="achievement-tile"], [data-testid="achievements-grid"] button').first().click()
      return waitDialog()
    }, { scope: 'dialog' })
    await audit('Settings', async () => goto('/settings', 'main h1'))
    for (const section of ['profile', 'appearance', 'notifications', 'data', 'devices', 'tokens', 'shortcut']) {
      await audit(`Settings · ${section}`, async () => goto(`/settings/${section}`, 'main'))
    }
    await audit(
      'Settings · integrations',
      async () => {
        await page.goto(`${WEB_URL}/settings/integrations`, { waitUntil: 'networkidle' })
        await page.waitForTimeout(400)
        return new URL(page.url()).pathname === '/settings/integrations'
      },
      { optional: true },
    )
    await audit(
      'sync sheet (header pill)',
      async () => {
        await goto('/today', '[data-testid="burned-today"]')
        const pill = page.locator('header > div > button').first()
        if (!(await pill.count()) || (await pill.isDisabled())) return false
        await pill.click()
        return topDialog()
          .waitFor({ timeout: 3000 })
          .then(() => true)
          .catch(() => false)
      },
      { scope: 'dialog', optional: true },
    )

    // onboarding wizard: the four steps, then «Пропустить»
    for (const [index, label] of ['profile', 'body', 'goal', 'limits'].entries()) {
      await audit(`onboarding · ${index + 1} ${label}`, async () => {
        await goto('/today', '[data-testid="burned-today"]')
        await page.evaluate(() => window.dispatchEvent(new CustomEvent('dsos:open-onboarding')))
        await page.getByText('Настроим расчёты').first().waitFor({ timeout: 8000 })
        for (let step = 0; step < index; step += 1) {
          await page.getByRole('button', { name: 'Далее' }).click()
          await page.waitForTimeout(350)
        }
        if (index === 2) {
          await page.getByRole('button', { name: 'Что получится' }).click()
          await page.waitForTimeout(800)
        }
        return true
      })
      await page.getByRole('button', { name: 'Пропустить' }).click().catch(() => {})
    }

    // pairing screen: a fresh context without a token (empty, then a refused code)
    const browser = context.browser()
    const userAgent = await page.evaluate(() => navigator.userAgent)
    const fresh = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
      userAgent,
      locale: 'ru-RU',
      timezoneId: 'Europe/Moscow',
    })
    const pairing = await fresh.newPage()
    const auditPairing = async (name, open) => {
      try {
        await open()
        await settle(400, pairing)
        const measured = await measureTargets(null, pairing)
        report.targets.push({ state: name, ...measured })
        check(
          measured.small.length === 0,
          `a11y ${name}: every target is at least 48 × 48 px`,
          measured.small.map((entry) => `${entry.selector} «${entry.name}» ${entry.size}`).slice(0, 6).join(' | '),
        )
        for (const theme of THEMES) {
          await setTheme(theme, pairing)
          await settle(300, pairing)
          const violations = await runAxe(null, pairing)
          totals.statesRun += 1
          const blocking = violations.filter((violation) => BLOCKING.has(violation.impact))
          totals.blocking += blocking.reduce((count, violation) => count + violation.nodes.length, 0)
          for (const violation of violations.filter((v) => !BLOCKING.has(v.impact))) {
            totals[violation.impact === 'moderate' ? 'moderate' : 'minor'] += violation.nodes.length
          }
          report.states.push({ state: name, theme, violations })
          const lines = formatNodes(blocking)
          check(blocking.length === 0, `axe ${name} (${theme}): no serious/critical violations`, lines.slice(0, 4).join(' | '))
          for (const line of lines) console.log(`         ${line}`)
          for (const line of formatNodes(violations.filter((v) => !BLOCKING.has(v.impact)))) console.log(`         note ${line}`)
        }
        await setTheme('dark', pairing)
      } catch (error) {
        bad(`a11y ${name}`, String(error?.message ?? error).split('\n')[0])
      }
    }
    await auditPairing('pairing screen', async () => {
      await pairing.goto(`${WEB_URL}/`, { waitUntil: 'networkidle' })
      await pairing.locator('#pairing-code').waitFor({ timeout: 10_000 })
    })
    await auditPairing('pairing screen · refused code', async () => {
      await pairing.locator('#pairing-code').fill('ZZZZ-ZZZZ')
      await pairing.getByRole('button', { name: 'Подключить' }).click()
      await pairing.waitForTimeout(1500)
    })
    await fresh.close()

    // ---------------------------------------------------------------------- //
    // 1b. live regions stay quiet unless something happened (timers tick silently)
    // ---------------------------------------------------------------------- //
    /** Text of every live region, keyed by element; a region that changes on every sample ticks aloud. */
    const sampleLive = () =>
      page.evaluate(() => {
        window.__a11yLive ??= new Map()
        const regions = document.querySelectorAll('[aria-live="polite"], [aria-live="assertive"], [role="status"], [role="alert"], [role="log"]')
        for (const el of regions) {
          if (el.getAttribute('aria-live') === 'off') continue
          const entry = window.__a11yLive.get(el) ?? { texts: [], selector: window.__a11yDescribe?.(el) ?? el.tagName }
          entry.texts.push((el.textContent ?? '').trim())
          window.__a11yLive.set(el, entry)
        }
      })
    const chattyRegions = () =>
      page.evaluate(() => {
        const out = []
        for (const entry of window.__a11yLive?.values() ?? []) {
          let changes = 0
          for (let index = 1; index < entry.texts.length; index += 1) if (entry.texts[index] !== entry.texts[index - 1]) changes += 1
          if (changes >= 3) out.push(`${entry.selector}: «${entry.texts.slice(-2).join('» → «')}»`)
        }
        window.__a11yLive = new Map()
        return out
      })
    const quietFor = async (label, open) => {
      try {
        await open()
        await settle(600)
        await inject()
        for (let sample = 0; sample < 5; sample += 1) {
          await sampleLive()
          await page.waitForTimeout(1050)
        }
        const chatty = await chattyRegions()
        check(chatty.length === 0, `live regions on ${label} do not re-announce on their own (running timers tick silently)`, chatty.slice(0, 3).join(' | '))
      } catch (error) {
        bad(`live regions on ${label}`, String(error?.message ?? error).split('\n')[0])
      }
    }
    await quietFor('Today with a running timer', () => goto('/today', '[data-testid="timer-bar"]'))
    await quietFor('a live workout during a rest', async () => {
      await goto(`/train/workout/${liveWorkout}`, '[data-testid="workout-exercise"]')
      await page.locator('[data-testid="save-set"]').first().click()
      await page.locator('[data-testid="rest-timer"]').waitFor({ timeout: 8000 })
    })

    // ---------------------------------------------------------------------- //
    // 2. keyboard pass
    // ---------------------------------------------------------------------- //
    console.log('\n9b. keyboard')
    await keyboardPass({ page, WEB_URL, check, ok, bad, report, settle, inject, editTask })

    // ---------------------------------------------------------------------- //
    // 3. live regions, reduced motion, language
    // ---------------------------------------------------------------------- //
    console.log('\n9c. live regions, reduced motion, language')
    await goto('/today', '[data-testid="burned-today"]')
    const water = page.getByRole('button', { name: /\+250 мл/ })
    await water.evaluate((el) => el.setAttribute('data-a11y-opener', ''))
    await water.focus()
    await water.press('Enter')
    const toast = page.locator('[role="status"][aria-live="polite"]').filter({ hasText: 'мл воды' })
    await toast.first().waitFor({ timeout: 8000 }).catch(() => {})
    await page.waitForTimeout(300)
    const afterToast = await page.evaluate(() => ({
      kept: !!document.activeElement?.hasAttribute('data-a11y-opener'),
      on: document.activeElement === document.body ? 'body' : (document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.textContent ?? '').trim().slice(0, 40),
    }))
    check(
      (await toast.count()) >= 1 && afterToast.kept,
      'a toast is announced in a polite status region and focus stays on the button that caused it',
      `focus on «${afterToast.on}»`,
    )
    const added = (await apiFetch('/v1/nutrition/day')).body?.water ?? []
    for (const log of Array.isArray(added) ? added : []) {
      if (log?.id && !created.water.includes(log.id) && log.amount_ml === 250) created.water.push(log.id)
    }

    await page.emulateMedia({ reducedMotion: 'reduce' })
    await goto('/today', '[data-testid="burned-today"]')
    const motion = await page.evaluate(async () => {
      const button = document.querySelector('nav.tabbar button[aria-label]')
      const transition = getComputedStyle(document.querySelector('.btn-secondary, .btn-primary') ?? document.body).transitionDuration
      button?.click()
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      const dialog = document.querySelector('[role="dialog"]')
      const transform = dialog ? getComputedStyle(dialog).transform : 'missing'
      return { transition, transform }
    })
    const settledTransform = (value) => value === 'none' || /matrix\(1, 0, 0, 1, 0, 0\)/.test(value)
    check(
      settledTransform(motion.transform) && parseFloat(motion.transition) < 0.01,
      'reduced motion: a sheet appears without sliding, CSS transitions are off',
      `sheet transform ${motion.transform}, button transition ${motion.transition}`,
    )
    await page.emulateMedia({ reducedMotion: null })

    await goto('/settings/appearance', 'main')
    const langBefore = await page.evaluate(() => document.documentElement.lang)
    await page.getByRole('tab', { name: 'English', exact: true }).click()
    await page.waitForTimeout(800)
    const langEnglish = await page.evaluate(() => document.documentElement.lang)
    await page.getByRole('tab', { name: 'Русский', exact: true }).click()
    await page.waitForTimeout(800)
    const langBack = await page.evaluate(() => document.documentElement.lang)
    check(
      langBefore === 'ru' && langEnglish === 'en' && langBack === 'ru',
      '<html lang> follows the UI language',
      `${langBefore} → ${langEnglish} → ${langBack}`,
    )
  } catch (error) {
    bad('stage 9 accessibility checks', String(error?.message ?? error).split('\n')[0])
  } finally {
    page.off('console', onConsole)
    page.off('pageerror', onPageError)
    await page.emulateMedia({ reducedMotion: null }).catch(() => {})
    await setTheme('dark').catch(() => {})
    check(errors.length === 0, 'no console errors during the accessibility checks', errors.slice(0, 2).join(' | '))

    try {
      writeFileSync(`${SHOTS}/a11y-axe.json`, JSON.stringify({ totals, ...report }, null, 2))
    } catch {
      /* the screenshots directory is optional */
    }
    ok(
      'axe summary',
      `${totals.statesRun} audits (screens × themes): ${totals.blocking} serious/critical, ${totals.moderate} moderate, ${totals.minor} minor node(s) — ${SHOTS}/a11y-axe.json`,
    )

    // clean up everything this module created
    const statuses = []
    if (created.timer) {
      await apiFetch('/v1/planner/timer/stop', { method: 'POST', body: JSON.stringify({ entry_id: created.timer }) }).catch(() => null)
      created.entries.push(created.timer)
    }
    for (const id of created.meals) statuses.push((await apiFetch(`/v1/nutrition/meals/${id}`, { method: 'DELETE' })).status)
    for (const id of created.water) statuses.push((await apiFetch(`/v1/nutrition/water/${id}`, { method: 'DELETE' })).status)
    if (created.steps) statuses.push((await apiFetch(`/v1/health/steps/${created.steps}`, { method: 'DELETE' })).status)
    if (created.sleep) statuses.push((await apiFetch(`/v1/health/sleep/${created.sleep}`, { method: 'DELETE' })).status)
    for (const id of created.workouts) statuses.push((await apiFetch(`/v1/training/workouts/${id}`, { method: 'DELETE' })).status)
    for (const id of created.entries) statuses.push((await apiFetch(`/v1/planner/time-entries/${id}`, { method: 'DELETE' })).status)
    for (const id of created.tasks) statuses.push((await apiFetch(`/v1/planner/tasks/${id}`, { method: 'DELETE' })).status)
    for (const id of created.resources) statuses.push((await apiFetch(`/v1/planner/resources/${id}`, { method: 'DELETE' })).status)
    for (const id of created.projects) statuses.push((await apiFetch(`/v1/planner/projects/${id}`, { method: 'DELETE' })).status)
    for (const id of created.areas) statuses.push((await apiFetch(`/v1/planner/areas/${id}`, { method: 'DELETE' })).status)
    const tags = (await apiFetch('/v1/planner/tags')).body?.items ?? []
    for (const tag of tags.filter((item) => item.name === 'a11y')) statuses.push((await apiFetch(`/v1/planner/tags/${tag.id}`, { method: 'DELETE' })).status)
    check(
      statuses.every((status) => status === 200 || status === 204),
      'accessibility test data removed',
      `${statuses.length} rows deleted${statuses.some((status) => status >= 300) ? `: ${statuses.join(',')}` : ''}`,
    )
  }
}

/**
 * Tab through Today, a sheet and the task editor. Focus must be visible on every stop
 * (an outline, a ring, a changed border or background), never hidden under the sticky
 * header or the fixed tab bar, trapped in an open sheet, and back on the opener after
 * Escape.
 */
async function keyboardPass({ page, WEB_URL, check, report, settle, inject, editTask }) {
  /** The unfocused look of every control not seen yet (run before each Tab: new ones appear as you go). */
  const snapshotFocusables = () =>
    page.evaluate(() => {
      window.__a11yBase ??= new Map()
      for (const el of document.querySelectorAll('a[href], button, input, select, textarea, [tabindex], [role="button"]')) {
        if (window.__a11yBase.has(el) || el === document.activeElement) continue
        const style = getComputedStyle(el)
        window.__a11yBase.set(el, [style.outlineStyle, style.outlineWidth, style.boxShadow, style.borderColor, style.backgroundColor].join('|'))
      }
    })
  const focusInfo = () =>
    page.evaluate(() => {
      const el = document.activeElement
      if (!el || el === document.body || el === document.documentElement) return null
      const style = getComputedStyle(el)
      const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 1 && !/rgba\(.*, 0\)$/.test(style.outlineColor)
      const now = [style.outlineStyle, style.outlineWidth, style.boxShadow, style.borderColor, style.backgroundColor].join('|')
      const base = window.__a11yBase?.get(el)
      const changed = base !== undefined && base !== now && (style.boxShadow !== 'none' || base.split('|')[3] !== style.borderColor || base.split('|')[4] !== style.backgroundColor)
      const rect = el.getBoundingClientRect()
      const x = Math.min(Math.max(rect.left + rect.width / 2, 1), window.innerWidth - 1)
      const y = Math.min(Math.max(rect.top + rect.height / 2, 1), window.innerHeight - 1)
      const hit = document.elementFromPoint(x, y)
      const inView = rect.bottom > 0 && rect.top < window.innerHeight
      const covered = !inView || !(hit && (hit === el || el.contains(hit) || hit.contains(el)))
      // every stop is numbered once: meeting a numbered element again means Tab came round
      const seen = el.hasAttribute('data-a11y-stop')
      if (!seen) el.setAttribute('data-a11y-stop', String(document.querySelectorAll('[data-a11y-stop]').length))
      return {
        seen,
        selector: window.__a11yDescribe(el),
        name: window.__a11yName(el),
        visible: outline || changed,
        covered,
        coveredBy: covered && hit ? window.__a11yDescribe(hit) : null,
        inDialog: !!el.closest('[role="dialog"]'),
      }
    })

  // ---- Today: every Tab stop ------------------------------------------------
  try {
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="burned-today"]').waitFor({ timeout: 15_000 })
    await settle()
    await inject()
    await snapshotFocusables()
    await page.evaluate(() => {
      document.activeElement?.blur?.()
      window.scrollTo(0, 0)
    })
    const stops = []
    for (let index = 0; index < 150; index += 1) {
      await snapshotFocusables()
      await page.keyboard.press('Tab')
      await page.waitForTimeout(60)
      const info = await focusInfo()
      if (!info) continue
      if (info.seen) break
      stops.push(info)
    }
    report.keyboard.push({ screen: 'Today', stops })
    const invisible = stops.filter((stop) => !stop.visible)
    const hidden = stops.filter((stop) => stop.covered)
    check(stops.length >= 15, 'keyboard: Tab reaches the controls of Today', `${stops.length} stops`)
    check(
      invisible.length === 0,
      'keyboard: focus is visible on every Tab stop of Today',
      invisible.slice(0, 5).map((stop) => `${stop.selector} «${stop.name}»`).join(' | '),
    )
    check(
      hidden.length === 0,
      'keyboard: a focused control is never hidden under the header or the tab bar',
      hidden.slice(0, 5).map((stop) => `${stop.selector} «${stop.name}» under ${stop.coveredBy}`).join(' | '),
    )
  } catch (error) {
    check(false, 'keyboard: Today', String(error?.message ?? error).split('\n')[0])
  }

  // ---- a sheet and the task editor: in, trapped, Escape, back -------------
  const sheetPass = async (label, path, ready, opener) => {
    try {
      await page.goto(`${WEB_URL}${path}`, { waitUntil: 'networkidle' })
      await page.locator(ready).first().waitFor({ timeout: 15_000 })
      await settle()
      await inject()
      const trigger = page.locator(opener).first()
      await trigger.focus()
      const openerName = await trigger.evaluate((el) => window.__a11yName(el))
      await page.keyboard.press('Enter')
      await page.locator('[role="dialog"]').last().waitFor({ timeout: 10_000 })
      await settle(500)
      const inside = await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))
      check(inside, `keyboard: ${label} — focus moves into the sheet when it opens`)
      await snapshotFocusables()
      const count = await page.evaluate(() => {
        const dialogs = document.querySelectorAll('[role="dialog"]')
        const dialog = dialogs[dialogs.length - 1]
        return [...dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter(
          (el) => !el.disabled && el.getBoundingClientRect().width > 0 && el.tabIndex >= 0,
        ).length
      })
      let escaped = 0
      let invisible = []
      for (let index = 0; index < count + 3; index += 1) {
        await snapshotFocusables()
        await page.keyboard.press('Tab')
        await page.waitForTimeout(50)
        const info = await focusInfo()
        if (!info?.inDialog) escaped += 1
        else if (!info.visible) invisible.push(`${info.selector} «${info.name}»`)
      }
      for (let index = 0; index < 3; index += 1) {
        await page.keyboard.press('Shift+Tab')
        await page.waitForTimeout(50)
        const info = await focusInfo()
        if (!info?.inDialog) escaped += 1
      }
      invisible = [...new Set(invisible)]
      check(escaped === 0, `keyboard: ${label} — Tab and Shift+Tab stay inside the open sheet`, `${count} controls, ${escaped} escape(s)`)
      check(invisible.length === 0, `keyboard: ${label} — focus is visible on every control in the sheet`, invisible.slice(0, 5).join(' | '))
      await page.keyboard.press('Escape')
      await page.locator('[role="dialog"]').waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})
      await page.waitForTimeout(300)
      const closed = (await page.locator('[role="dialog"]').count()) === 0
      const back = await page.evaluate(() => (document.activeElement ? window.__a11yName(document.activeElement) : ''))
      check(closed, `keyboard: ${label} — Escape closes the sheet`)
      check(closed && back === openerName, `keyboard: ${label} — focus returns to the control that opened it`, `«${back}»`)
    } catch (error) {
      check(false, `keyboard: ${label}`, String(error?.message ?? error).split('\n')[0])
    }
  }
  await sheetPass('quick-add sheet', '/today', '[data-testid="burned-today"]', 'nav.tabbar button[aria-label]')
  await sheetPass(
    'task editor',
    '/plan',
    '[data-testid="plan-today"]',
    `[data-testid="task-row"][data-task-id="${editTask}"] button:has([data-testid="task-title"])`,
  )
}
