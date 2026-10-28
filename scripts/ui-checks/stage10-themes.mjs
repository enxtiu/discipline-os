/**
 * Browser checks — stage 10, part 1: themes. Called by scripts/ui-check.mjs before the
 * stage-9 accessibility audit (which stays last); leaves the appearance as it found it
 * («Эволюция», dark) and removes what it creates.
 *
 *  - Настройки → Оформление: the hub entry, the theme gallery (a radiogroup of nine,
 *    two selectable, the rest «Скоро» with aria-disabled; arrows move and choose), mode
 *    light / dark / system (following the OS live), the stickers' parameters (ground,
 *    tilt, shadows) and the two switches for every theme — each one on <html>
 *    (data-skin, data-theme, data-ground, data-tilt, data-shadow, data-text,
 *    data-motion), <meta name="theme-color">, stored per device and applied before the
 *    app code runs after a reload (no flash).
 *  - The level for every theme: Сегодня's status (score, streak, level, XP to the next
 *    level) and Статистика's plaque (level, title, XP to go, total, this week, today,
 *    the streak) = the gamification API, in «Эволюция» and in «Наклейки»; no freeze
 *    anywhere (the owner's decision).
 *  - Theme F «Наклейки»: its widget forms (calorie sticker, macro bars, water/steps
 *    tiles, the floating «＋ Записать», «Наклейки недели» when something was unlocked
 *    this week), Rubik in use, outlined chart bars.
 *  - axe-core (WCAG 2.1 A/AA, serious + critical = 0) and the 48 px target rule on
 *    «Оформление» (dark and light) and on the stickers skin's Сегодня, Питание, Статистика,
 *    Тренировки, План and the quick-add sheet, in Сирень (light) and Ночь (dark).
 *  - The polish against the final boards (Ffin-*): the tab's title and the page's buttons in
 *    the header row, Питание's day switch / day totals / meal cards / ИИ tags, the quick add's
 *    order, plan tag and recents, the Plan's priority stickers, Статистика's plaque form.
 *  - «Крупный текст» in both themes: no screen wider than the 390 px screen.
 *  - Screenshots s10-*.png at 390 × 844 in $SHOTS.
 */
import { randomUUID } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
const BLOCKING = new Set(['serious', 'critical'])
const MIN_TARGET = 48
const GROUND = { lilac: '#E8EAFB', mint: '#DBF1E5', peach: '#FBE6DC', lemonade: '#F0EFD2' }
const NIGHT = '#17161F'

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
  window.__s10Describe = (el) => {
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

export async function run(ctx) {
  const { context, WEB_URL, SHOTS, check, ok, bad } = ctx
  console.log('\n10a. stage 10 themes — Оформление, the level for every theme, «Наклейки»')
  // a tab of its own (as the stage-9 audit): the shared tab rests on about:blank meanwhile
  await ctx.page.goto('about:blank').catch(() => {})
  const page = await context.newPage()
  const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png` })
  const errors = []
  page.on('console', (message) => {
    if (message.type() === 'error' && !/favicon|404 \(Not Found\)|Failed to load resource/i.test(message.text())) errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(String(error)))
  const axe = loadAxe()
  const totals = { audits: 0, blocking: 0, moderate: 0, minor: 0 }
  const report = []
  const created = { meals: [], water: [], steps: null, tasks: [] }

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
  const attrs = () =>
    page.evaluate(() => {
      const root = document.documentElement
      return {
        ...root.dataset,
        meta: document.querySelector('meta[name="theme-color"]')?.getAttribute('content') ?? null,
        scheme: getComputedStyle(root).colorScheme,
        bg: getComputedStyle(document.body).backgroundColor,
        font: getComputedStyle(document.body).fontFamily,
        size: getComputedStyle(root).fontSize,
      }
    })
  /** Store an appearance and reload (as a returning user would open the app). */
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
  const goto = async (path, ready) => {
    if (/^\/(today|stats)/.test(path)) await markSeen().catch(() => {})
    await page.goto(`${WEB_URL}${path}`, { waitUntil: 'networkidle' })
    if (ready) await page.locator(ready).first().waitFor({ timeout: 15_000 })
    await settle()
  }
  const inject = async () => {
    const missing = await page.evaluate(() => typeof window.axe === 'undefined' || typeof window.__s10Describe === 'undefined')
    if (missing) {
      await page.addScriptTag({ content: axe.source })
      await page.addScriptTag({ content: describeSource })
    }
  }
  const runAxe = async (scope) => {
    await inject()
    return page.evaluate(
      async ({ scope, tags }) => {
        let root = document
        if (scope === 'dialog') {
          const dialogs = [...document.querySelectorAll('[role="dialog"]')]
          root = dialogs[dialogs.length - 1] ?? document
        }
        const result = await window.axe.run(root, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] })
        return result.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          nodes: violation.nodes.map((node) => ({ target: node.target.join(' '), summary: (node.failureSummary ?? '').replace(/\s+/g, ' ').slice(0, 240) })),
        }))
      },
      { scope, tags: AXE_TAGS },
    )
  }
  /** Operable controls under 48 × 48 px (the stage-9 rule: covered tab-bar targets count as small). */
  const smallTargets = async (scope) => {
    await inject()
    return page.evaluate(
      ({ scope, min }) => {
        const dialogs = [...document.querySelectorAll('[role="dialog"]')]
        const root = scope === 'dialog' ? (dialogs[dialogs.length - 1] ?? document.body) : document.body
        const selector = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="link"], [role="tab"], [role="switch"], [role="checkbox"], [role="radio"], [tabindex]:not([tabindex="-1"])'
        const small = []
        for (const el of root.querySelectorAll(selector)) {
          if (scope !== 'dialog' && dialogs.some((dialog) => dialog.contains(el))) continue
          if (el.closest('[aria-hidden="true"], [inert]') || el.disabled) continue
          const style = getComputedStyle(el)
          if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') continue
          const rect = el.getBoundingClientRect()
          if (rect.width <= 2 || rect.height <= 2) continue
          if (el.closest('nav.tabbar, header.sticky')) {
            const x = rect.left + rect.width / 2
            const y = rect.top + rect.height / 2
            const hit = x >= 0 && y >= 0 && x <= window.innerWidth && y <= window.innerHeight ? document.elementFromPoint(x, y) : null
            if (hit && !(hit === el || el.contains(hit))) {
              small.push(`${window.__s10Describe(el)} covered by ${window.__s10Describe(hit)}`)
              continue
            }
          }
          if (rect.width + 0.5 >= min && rect.height + 0.5 >= min) continue
          const label = el.matches('input') ? el.closest('label') : null
          if (label) {
            const box = label.getBoundingClientRect()
            if (box.width + 0.5 >= min && box.height + 0.5 >= min) continue
          }
          const parentText = (el.parentElement?.textContent ?? '').trim().length
          if (el.tagName === 'A' && style.display === 'inline' && parentText > (el.textContent ?? '').trim().length + 3) continue
          // the chart bars and calendar blocks the stage-9 audit documents as exceptions
          if (el.closest('[data-testid="burned-chart"] li > button, [data-testid="steps-chart"] [role="listitem"] > button, [data-testid="cal-day-header"], [data-testid="cal-block"]')) continue
          small.push(`${window.__s10Describe(el)} ${Math.round(rect.width)}×${Math.round(rect.height)}`)
        }
        const widened = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
        return { small, widened }
      },
      { scope, min: MIN_TARGET },
    )
  }
  /** One state: 48 px targets and axe, both blocking. */
  const audit = async (name, scope = null, screenshot = null) => {
    try {
      const targets = await smallTargets(scope)
      check(targets.small.length === 0, `a11y ${name}: every target is at least 48 × 48 px`, targets.small.slice(0, 5).join(' | '))
      if (targets.widened) bad(`a11y ${name}: nothing makes the page wider than the screen`)
      const violations = await runAxe(scope)
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

  try {
    if (!axe) {
      bad('axe-core is installed (web/node_modules/axe-core)', 'run `npm install` in web/')
      return
    }
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    await appearance('dark', null)

    // -------------------------------------------------------------------- //
    // seed: a day with food, water and steps (removed at the end)
    // -------------------------------------------------------------------- //
    const day = (await apiFetch('/v1/nutrition/day')).body
    const T = day?.date
    const statuses = []
    for (const [type, name, grams, kcal] of [
      ['breakfast', 'Овсянка на молоке (ui-check s10)', 250, 116],
      ['lunch', 'Гречка отварная (ui-check s10)', 200, 110],
    ]) {
      const meal = await apiFetch('/v1/nutrition/meals', {
        method: 'POST',
        body: JSON.stringify({
          id: randomUUID(),
          meal_type: type,
          source: 'manual',
          items: [{ name, grams, kcal_per_100g: kcal, protein_per_100g: 4, fat_per_100g: 2.5, carbs_per_100g: 19, fiber_per_100g: 2.5 }],
        }),
      })
      statuses.push(meal.status)
      if (meal.status === 200 || meal.status === 201) created.meals.push(meal.body?.id)
    }
    const water = await apiFetch('/v1/nutrition/water', { method: 'POST', body: JSON.stringify({ id: randomUUID(), amount_ml: 750 }) })
    statuses.push(water.status)
    if (water.status === 200 || water.status === 201) created.water.push(water.body?.id)
    if ((await apiFetch('/v1/health/today')).body?.day?.steps == null) {
      const steps = await apiFetch(`/v1/health/steps/${T}`, { method: 'PUT', body: JSON.stringify({ steps: 6420 }) })
      statuses.push(steps.status)
      if (steps.status === 200) created.steps = T
    }
    check(statuses.every((status) => status === 200 || status === 201), 'seeded a day for the theme checks: meals, water, steps', `${statuses.length} writes on ${T}`)

    // -------------------------------------------------------------------- //
    // 1. Настройки → Оформление
    // -------------------------------------------------------------------- //
    await goto('/settings', 'main h1')
    const hubRow = page.locator('a[href="/settings/theme"]')
    check(
      (await hubRow.count()) === 1 && /Оформление/.test(await hubRow.innerText()) && /Эволюция · Тёмная/.test(await hubRow.innerText()),
      'Настройки: «Оформление» with the theme and the mode as its hint',
      (await hubRow.innerText().catch(() => '')).replace(/\s+/g, ' '),
    )
    await hubRow.click()
    await page.waitForURL(/\/settings\/theme$/, { timeout: 8000 })
    await page.locator('[data-testid="theme-gallery"]').waitFor({ timeout: 10_000 })
    await settle()
    const gallery = await page.evaluate(() => {
      const group = document.querySelector('[role="radiogroup"]')
      const radios = [...group.querySelectorAll('[role="radio"]')]
      return {
        name: group.getAttribute('aria-label'),
        count: radios.length,
        checked: radios.filter((radio) => radio.getAttribute('aria-checked') === 'true').map((radio) => radio.dataset.skinOption),
        enabled: radios.filter((radio) => radio.getAttribute('aria-disabled') !== 'true').map((radio) => radio.dataset.skinOption),
        soon: radios.filter((radio) => radio.getAttribute('aria-disabled') === 'true' && /Скоро/.test(radio.textContent ?? '')).length,
        tabStops: radios.filter((radio) => radio.tabIndex === 0).length,
      }
    })
    check(
      gallery.name === 'Тема' && gallery.count === 9 && gallery.checked.join() === 'evolution' && gallery.enabled.sort().join() === 'evolution,stickers' && gallery.soon === 7 && gallery.tabStops === 1,
      '«Тема»: a radiogroup of nine — Эволюция checked, Наклейки selectable, seven «Скоро» (aria-disabled), one Tab stop',
      JSON.stringify(gallery),
    )
    await audit('Оформление (Эволюция, dark)', null, 's10-01-theme-evolution-dark')
    await appearance('light', null)
    await goto('/settings/theme', '[data-testid="theme-gallery"]')
    await audit('Оформление (Эволюция, light)')
    await appearance('dark', null)
    await goto('/settings/theme', '[data-testid="theme-gallery"]')

    // the arrows (the order is A–I): from Эволюция (G) left to Наклейки (F), back, then on to Бережный (H, «Скоро»)
    await page.locator('[role="radio"][aria-checked="true"]').focus()
    await page.keyboard.press('ArrowLeft') // G → F (Наклейки): chosen
    await page.waitForTimeout(250)
    let state = await attrs()
    const focusedLeft = await page.evaluate(() => document.activeElement?.getAttribute('data-skin-option'))
    await page.keyboard.press('ArrowRight') // F → G (Эволюция): chosen again
    await page.keyboard.press('ArrowRight') // G → H (Бережный, «Скоро»): focus moves, nothing chosen
    await page.waitForTimeout(250)
    const afterReserved = await attrs()
    const focusedReserved = await page.evaluate(() => document.activeElement?.getAttribute('data-skin-option'))
    check(
      state.skin === 'stickers' && focusedLeft === 'stickers' && afterReserved.skin === 'evolution' && focusedReserved === 'gentle',
      'gallery keyboard: arrows move and choose; a «Скоро» theme takes the focus but is never chosen',
      `← ${focusedLeft}/${state.skin}, → → ${focusedReserved}/${afterReserved.skin}`,
    )

    // choose Наклейки with a click: the whole app follows at once, the parameters appear
    await page.locator('[role="radio"][data-skin-option="stickers"]').click()
    await page.locator('[data-testid="theme-stickers-params"]').waitFor({ timeout: 5000 })
    await settle()
    state = await attrs()
    const stored = await page.evaluate(() => JSON.parse(window.localStorage.getItem('dsos.appearance') ?? '{}'))
    check(
      state.skin === 'stickers' && state.theme === 'dark' && state.meta === NIGHT && stored.skin === 'stickers' && /Rubik/.test(state.font),
      'Наклейки chosen: data-skin on <html>, the night set in dark mode (theme-color #17161F), Rubik, stored on the device',
      `${state.skin} ${state.theme} ${state.meta} «${state.font.split(',')[0]}»`,
    )
    await page.getByRole('tab', { name: 'Светлая', exact: true }).click()
    await page.waitForTimeout(300)
    state = await attrs()
    check(
      state.theme === 'light' && state.meta === GROUND.lilac && state.scheme === 'light' && state.bg === 'rgb(232, 234, 251)',
      'Режим «Светлая»: data-theme light, the lilac ground, theme-color #E8EAFB, color-scheme light',
      `${state.theme} ${state.meta} ${state.scheme} ${state.bg}`,
    )
    await audit('Оформление (Наклейки, Сирень)', null, 's10-02-theme-stickers-lilac')
    await page.locator('[data-ground-option="mint"]').click()
    await page.waitForTimeout(300)
    state = await attrs()
    check(
      state.ground === 'mint' && state.meta === GROUND.mint && state.bg === 'rgb(219, 241, 229)' &&
        (await page.locator('[data-ground-option="mint"]').getAttribute('aria-pressed')) === 'true',
      '«Набор цветов» Мята: data-ground mint, the page and theme-color follow, the swatch is pressed',
      `${state.ground} ${state.meta} ${state.bg}`,
    )
    await page.locator('[data-ground-option="lilac"]').click()
    // tilt: a badge leans while «Наклон наклеек» is on, stands straight when off (a probe
    // badge: the rule is the theme's, whatever screen it is on)
    const tiltOf = () =>
      page.evaluate(() => {
        const probe = document.createElement('span')
        probe.className = 'sk-tilt-r'
        document.querySelector('main').append(probe)
        const rotate = getComputedStyle(probe).rotate
        probe.remove()
        return rotate
      })
    const tiltOn = await tiltOf()
    await page.getByRole('switch', { name: 'Наклон наклеек' }).click()
    await page.waitForTimeout(250)
    const tiltOff = await tiltOf()
    state = await attrs()
    check(state.tilt === 'off' && tiltOn !== 'none' && tiltOff === 'none', '«Наклон наклеек»: badges lean only while it is on', `${tiltOn} → ${tiltOff}`)
    await page.getByRole('switch', { name: 'Наклон наклеек' }).click()
    // shadows: hard offset → soft blur
    const shadowOf = () => page.evaluate(() => getComputedStyle(document.querySelector('[data-testid="theme-stickers-params"]')).boxShadow)
    const hard = await shadowOf()
    await page.locator('[data-testid="look-shadow-soft"]').click()
    await page.waitForTimeout(250)
    const soft = await shadowOf()
    state = await attrs()
    check(
      state.shadow === 'soft' && /4px 4px 0px/.test(hard) && !/4px 4px 0px/.test(soft) && /14px/.test(soft),
      '«Тени» Мягкие: the hard 4 px offset becomes a soft drop',
      `${hard} → ${soft}`,
    )
    await page.locator('[data-testid="look-shadow-hard"]').click()
    // large text and less motion, for every theme
    await page.getByRole('switch', { name: 'Крупный текст' }).click()
    await page.waitForTimeout(250)
    state = await attrs()
    check(state.text === 'large' && state.size === '18px', '«Крупный текст»: data-text large, the root type 16 → 18 px', `${state.text} ${state.size}`)
    await page.getByRole('switch', { name: 'Крупный текст' }).click()
    await page.getByRole('switch', { name: 'Меньше анимации' }).click()
    await page.waitForTimeout(250)
    state = await attrs()
    const motion = await page.evaluate(() => {
      const probe = document.createElement('span')
      probe.className = 'sk-tilt-r'
      document.querySelector('main').append(probe)
      const out = {
        transition: getComputedStyle(document.querySelector('.btn-secondary, .btn-primary, .chip') ?? document.body).transitionDuration,
        tilt: getComputedStyle(probe).rotate,
      }
      probe.remove()
      return out
    })
    check(
      state.motion === 'reduce' && parseFloat(motion.transition) < 0.01 && motion.tilt === 'none',
      '«Меньше анимации»: data-motion reduce — CSS transitions off and no tilt, without the OS setting',
      `${state.motion} ${motion.transition} ${motion.tilt}`,
    )
    await page.getByRole('switch', { name: 'Меньше анимации' }).click()
    // «Как в системе» follows the OS, live
    await page.getByRole('tab', { name: 'Как в системе', exact: true }).click()
    await page.emulateMedia({ colorScheme: 'dark' })
    await page.waitForTimeout(250)
    const systemDark = await attrs()
    await page.emulateMedia({ colorScheme: 'light' })
    await page.waitForTimeout(250)
    const systemLight = await attrs()
    check(
      systemDark.theme === 'dark' && systemDark.meta === NIGHT && systemLight.theme === 'light' && systemLight.meta === GROUND.lilac &&
        (await page.evaluate(() => window.localStorage.getItem('dsos.theme'))) === 'system',
      '«Как в системе»: the OS switch repaints at once (night ↔ lilac), the mode is stored as system',
      `${systemDark.theme}/${systemDark.meta} → ${systemLight.theme}/${systemLight.meta}`,
    )
    await page.emulateMedia({ colorScheme: null })
    // a reload: everything is back before the app code runs (the inline script in index.html)
    await page.getByRole('tab', { name: 'Светлая', exact: true }).click()
    await page.locator('[data-ground-option="peach"]').click()
    await page.evaluate(() => {
      window.sessionStorage.setItem('s10-early', '1')
    })
    await page.addInitScript(() => {
      if (!window.sessionStorage.getItem('s10-early')) return
      document.addEventListener('readystatechange', () => {
        if (document.readyState !== 'interactive' || window.__s10Early) return
        // the document is parsed, the module scripts have not run yet
        const root = document.documentElement
        window.__s10Early = { ...root.dataset, meta: document.querySelector('meta[name="theme-color"]')?.getAttribute('content') }
      })
    })
    await page.reload({ waitUntil: 'networkidle' })
    const early = await page.evaluate(() => window.__s10Early ?? null)
    check(
      early?.skin === 'stickers' && early?.theme === 'light' && early?.ground === 'peach' && early?.meta === GROUND.peach && early?.tilt === 'on' && early?.shadow === 'hard',
      'reload: theme, mode, ground, tilt and shadows are on <html> before any app code runs (no flash)',
      JSON.stringify(early),
    )
    await page.evaluate(() => window.sessionStorage.removeItem('s10-early'))

    // -------------------------------------------------------------------- //
    // 2. The level for every theme: Сегодня's status and Статистика's plaque = the API
    // -------------------------------------------------------------------- //
    const levelChecks = async (skinLabel) => {
      await goto('/today', '[data-testid="today-status"]')
      const game = (await apiFetch('/v1/gamification/today')).body
      const overall = game.streaks.find((streak) => streak.kind === 'overall')
      const status = await page.evaluate(() => {
        const level = document.querySelector('[data-testid="status-level"]')
        return {
          level: Number(level?.dataset.level),
          total: Number(level?.dataset.total),
          title: document.querySelector('[data-testid="status-level-title"]')?.textContent ?? '',
          next: (document.querySelector('[data-testid="status-xp-next"]')?.textContent ?? '').replace(/\s/g, ' '),
          score: Number(document.querySelector('[data-testid="status-score"]')?.dataset.value),
          streak: Number(document.querySelector('[data-testid="status-streak"]')?.dataset.current),
          note: document.querySelector('[data-testid="status-streak-note"]')?.textContent ?? '',
          text: document.querySelector('[data-testid="today-status"]')?.textContent ?? '',
        }
      })
      const toNext = game.xp.level.next_min_xp != null ? game.xp.level.next_min_xp - game.xp.total : null
      check(
        status.level === game.xp.level.level &&
          status.total === game.xp.total &&
          status.title.includes(game.xp.level.title_ru) &&
          (toNext == null || status.next.includes(String(toNext).replace(/\B(?=(\d{3})+(?!\d))/g, ' '))) &&
          status.score === game.score.value &&
          status.streak === (overall?.current ?? 0) &&
          status.note === 'Пропуск обнулит серию' &&
          !/замороз/i.test(status.text),
        `Сегодня (${skinLabel}): score, streak, «ур. ${game.xp.level.level} · ${game.xp.level.title_ru}», XP and XP to the next level = GET /gamification/today; no freeze`,
        `${status.title} · ${status.total} XP · ${status.next} · score ${status.score} · streak ${status.streak}`,
      )
      await goto('/stats', '[data-testid="level-plaque"]')
      const profile = (await apiFetch('/v1/gamification/profile')).body
      const week = (await apiFetch('/v1/stats/progress?period=week')).body
      const weekXp = (week?.xp ?? []).reduce((sum, point) => sum + point.xp, 0)
      const plaque = await page.evaluate(() => {
        const root = document.querySelector('[data-testid="level-plaque"]')
        const read = (id, key = 'xp') => Number(document.querySelector(`[data-testid="${id}"]`)?.dataset[key])
        const header = document.querySelector('[data-testid="stats-page"] > header')
        const switcher = document.querySelector('[data-testid="stats-period"]')
        return {
          level: Number(root?.dataset.level),
          total: Number(root?.dataset.total),
          title: document.querySelector('#level-plaque-title')?.textContent ?? '',
          left: read('plaque-xp-left'),
          week: read('plaque-week'),
          today: read('plaque-today'),
          streak: read('plaque-streak', 'current'),
          bar: document.querySelector('[data-testid="level-plaque"] [role="progressbar"]')?.getAttribute('aria-valuetext') ?? '',
          // at the top: after the title row, before the period switch
          placed:
            !!header && !!switcher && !!root &&
            !!(header.compareDocumentPosition(root) & Node.DOCUMENT_POSITION_FOLLOWING) &&
            !!(root.compareDocumentPosition(switcher) & Node.DOCUMENT_POSITION_FOLLOWING),
          text: root?.textContent ?? '',
        }
      })
      const pl = profile.xp.level
      check(
        plaque.placed &&
          plaque.level === pl.level &&
          plaque.total === profile.xp.total &&
          plaque.title.includes(pl.title_ru) &&
          (pl.next_min_xp == null || plaque.left === pl.next_min_xp - profile.xp.total) &&
          plaque.week === weekXp &&
          plaque.today === profile.xp.today &&
          plaque.streak === (profile.streaks.find((streak) => streak.kind === 'overall')?.current ?? 0) &&
          /из .* XP/.test(plaque.bar) &&
          !/замороз/i.test(plaque.text),
        `Статистика (${skinLabel}): the plaque at the top — level, title, XP to go, total, this week, today, streak = the API; no freeze`,
        `ур. ${plaque.level} «${pl.title_ru}» · ${plaque.total} XP · осталось ${plaque.left} · неделя +${plaque.week} · сегодня +${plaque.today} · «${plaque.bar}»`,
      )
    }
    await appearance('dark', null)
    await levelChecks('Эволюция')
    await appearance('light', { skin: 'stickers', ground: 'lilac', tilt: true, shadow: 'hard' })
    await levelChecks('Наклейки')

    // -------------------------------------------------------------------- //
    // 3. «Наклейки»: its forms, Rubik, outlined bars; audits in Сирень and Ночь
    // -------------------------------------------------------------------- //
    await goto('/today', '[data-testid="calorie-sticker"]')
    const forms = await page.evaluate(() => ({
      sticker: !!document.querySelector('[data-testid="calorie-sticker"] [role="progressbar"][aria-label]'),
      left: document.querySelector('[data-testid="calorie-sticker-left"]')?.textContent ?? '',
      ring: !!document.querySelector('main .sk-ring[role="progressbar"][aria-label="Калории"]'),
      macros: document.querySelectorAll('[data-testid="macro-bars"] [role="progressbar"]').length,
      tiles: document.querySelectorAll('[data-testid="water-steps-tiles"] > [data-cat]').length,
      add: (() => {
        const button = document.querySelector('[data-testid="quick-add-button"]')
        return button ? { text: button.textContent?.trim(), popup: button.getAttribute('aria-haspopup'), tabs: document.querySelectorAll('nav.tabbar a.tab').length } : null
      })(),
      medal: !!document.querySelector('[data-testid="status-level"] .sk-medal'),
      font: getComputedStyle(document.body).fontFamily,
      rubik: [...document.fonts].some((face) => face.family.replace(/"/g, '') === 'Rubik' && face.status === 'loaded'),
    }))
    check(
      forms.sticker && /Осталось \d/.test(forms.left.replace(/\s/g, ' ')) && !forms.ring && forms.macros === 4 && forms.tiles === 2 && forms.medal &&
        forms.add?.text === 'Записать' && forms.add?.popup === 'dialog' && forms.add?.tabs === 5,
      'Наклейки on Сегодня: the calorie sticker (no ring), four macro bars, water and steps tiles, the level medal, «＋ Записать» (aria-haspopup) with five tabs',
      JSON.stringify({ ...forms, font: undefined }),
    )
    check(/Rubik/.test(forms.font) && forms.rubik, 'Наклейки: Rubik is the type and its files loaded (self-hosted)', forms.font.split(',')[0])
    await audit('Наклейки · Сегодня (Сирень)', null, 's10-10-stickers-today-lilac')
    await page.locator('[data-testid="quick-add-button"]').click()
    await page.locator('[role="dialog"]').last().waitFor({ timeout: 8000 })
    await settle(600)
    await audit('Наклейки · быстрое добавление (Сирень)', 'dialog', 's10-11-stickers-add-lilac')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
    const focusBack = await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))
    check(focusBack === 'quick-add-button', 'Наклейки: Escape closes the sheet and the focus returns to «Записать»', String(focusBack))
    await goto('/food', 'input[placeholder*="Поиск"]')
    await audit('Наклейки · Питание (Сирень)', null, 's10-12-stickers-food-lilac')
    await goto('/stats', '[data-testid="level-plaque"]')
    await page
      .waitForFunction(() => document.querySelectorAll('[data-testid^="stats-section-"]').length === 5 && !document.querySelector('[data-testid^="stats-skeleton-"]'), null, { timeout: 20_000 })
      .catch(() => {})
    await settle()
    const bars = await page.evaluate(() => {
      const paths = [...document.querySelectorAll('[data-testid="chart-kcal"] .stats-bar-kcal .recharts-bar-rectangle path')]
      return paths.map((path) => ({ stroke: path.getAttribute('stroke'), width: path.getAttribute('stroke-width') }))
    })
    check(
      bars.length > 0 && bars.every((bar) => bar.stroke && bar.stroke !== 'none' && Number(bar.width) >= 1.5),
      'Наклейки · Статистика: the calorie bars are outlined (the chart reads the theme)',
      JSON.stringify(bars.slice(0, 3)),
    )
    const unlockedThisWeek = await page.evaluate(() => document.querySelector('[data-testid="week-stickers"]')?.getAttribute('data-count') ?? '0')
    ok('Наклейки · Статистика: «Наклейки недели»', Number(unlockedThisWeek) > 0 ? `${unlockedThisWeek} sticker(s) this week` : 'nothing unlocked this week — the strip steps aside')
    await audit('Наклейки · Статистика (Сирень)', null, 's10-13-stickers-stats-lilac')
    await goto('/train', '[data-testid="week-strip"]')
    await audit('Наклейки · Тренировки (Сирень)', null, 's10-14-stickers-train-lilac')
    await goto('/plan', '[data-testid="plan-today"]')
    await audit('Наклейки · План (Сирень)', null, 's10-15-stickers-plan-lilac')

    // -------------------------------------------------------------------- //
    // 3b. The polish against the final boards (Ffin-*), «Наклейки» in Сирень
    // -------------------------------------------------------------------- //
    const prio = await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ id: randomUUID(), title: 'Задача с приоритетом (ui-check s10)', priority: 1, due_date: T }) })
    if (prio.status === 201 || prio.status === 200) created.tasks.push(prio.body?.id)
    const headerOf = () =>
      page.evaluate(() => {
        const header = document.querySelector('header.sk-header')
        const title = header?.querySelector('[data-testid="header-title"]')
        const h1 = document.querySelector('main h1')
        const sr = h1 ? getComputedStyle(h1) : null
        return {
          title: title?.textContent?.trim() ?? null,
          h1: h1?.textContent?.trim() ?? null,
          h1Hidden: !!sr && sr.position === 'absolute' && sr.width === '1px',
          actions: [...(header?.querySelectorAll('.sk-actions button, .sk-actions a') ?? [])].map((el) => el.getAttribute('aria-label') ?? el.textContent?.trim()),
          sync: !!header?.querySelector('[data-testid="sync-pill"]'),
          settings: !!header?.querySelector('button[aria-label="Настройки"]'),
          fontSize: title ? getComputedStyle(title).fontSize : null,
          row: header ? Math.round(header.getBoundingClientRect().height) : null,
        }
      })
    for (const [path, ready, name, actions] of [
      ['/food', 'input[placeholder*="Поиск"]', 'Питание', 0],
      ['/train', '[data-testid="week-strip"]', 'Тренировки', 0],
      ['/stats', '[data-testid="level-plaque"]', 'Статистика', 1],
      ['/plan', '[data-testid="plan-today"]', 'План', 2],
    ]) {
      await goto(path, ready)
      const head = await headerOf()
      check(
        head.title === name && head.h1?.includes(name) && head.h1Hidden && head.sync && head.settings && head.actions.length === actions,
        `Наклейки · ${name}: the title shares the header row with sync, settings${actions ? ` and the page's ${actions} button(s)` : ''}; the h1 stays for screen readers`,
        JSON.stringify(head),
      )
    }
    // Питание: the day switch, the day total, the meal cards, the ИИ tags
    await goto('/food', 'input[placeholder*="Поиск"]')
    const dayApi = (await apiFetch('/v1/nutrition/day')).body
    const food = await page.evaluate(() => {
      const digits = (text) => Number((text ?? '').replace(/\D/g, ''))
      const label = document.querySelector('[data-testid="day-switch"] time')
      return {
        label: label?.textContent ?? '',
        today: /сегодня/.test(document.querySelector('[data-testid="day-switch"]')?.textContent ?? ''),
        next: document.querySelector('[data-testid="day-switch"] button[aria-label="Следующий день"]')?.disabled,
        total: digits(document.querySelector('[data-testid="day-totals-value"]')?.textContent),
        bars: document.querySelectorAll('[data-testid="day-totals"] [role="progressbar"]').length,
        cards: [...document.querySelectorAll('[data-testid="meal-card"]')].map((card) => card.querySelector('h2')?.firstChild?.textContent?.trim()),
        rows: document.querySelectorAll('[data-testid="meal-row"]').length,
        add: document.querySelectorAll('.sk-meal-add').length,
        tags: [...document.querySelectorAll('.sk-capture .sk-ai-tag')].map((tag) => tag.textContent),
        capture: [...document.querySelectorAll('.sk-capture > *')].map((el) => Math.round(el.getBoundingClientRect().height)),
      }
    })
    const types = new Set((dayApi.meals ?? []).map((meal) => meal.meal_type))
    check(
      /^[А-Яа-я]{2}, \d{1,2} [а-я]+$/.test(food.label) && food.today && food.next === true && food.total === Math.round(dayApi.totals.kcal) && food.bars === 5 &&
        food.cards.length === types.size && food.rows === (dayApi.meals ?? []).length && food.add === food.cards.length,
      'Наклейки · Питание: the day switch («Ср, 28 октября» · сегодня), the day total = GET /nutrition/day, a card per meal type with «＋»',
      JSON.stringify({ label: food.label, total: food.total, cards: food.cards, api: Math.round(dayApi.totals.kcal) }),
    )
    check(
      food.tags.length === 2 && food.tags.every((tag) => tag === 'ИИ') && food.capture.length === 3 && food.capture.every((height) => height === 48),
      'Наклейки · Питание: Фото and Текст carry the «ИИ» tag; the three ways to log are one 48 px row',
      JSON.stringify({ tags: food.tags, capture: food.capture }),
    )
    await page.locator('[data-testid="day-switch"] button[aria-label="Предыдущий день"]').click()
    await page.waitForFunction((was) => document.querySelector('[data-testid="day-switch"] time')?.getAttribute('datetime') !== was, await page.locator('[data-testid="day-switch"] time').getAttribute('datetime'), { timeout: 8000 }).catch(() => {})
    await settle(500)
    const yesterday = await page.evaluate(() => ({
      today: /сегодня/.test(document.querySelector('[data-testid="day-switch"]')?.textContent ?? ''),
      next: document.querySelector('[data-testid="day-switch"] button[aria-label="Следующий день"]')?.disabled,
      add: document.querySelectorAll('.sk-meal-add').length,
    }))
    check(
      !yesterday.today && yesterday.next === false && yesterday.add === 0,
      'Наклейки · Питание: «Предыдущий день» shows the day before (no «сегодня», «Следующий день» enabled, no «＋» on a past day)',
      JSON.stringify(yesterday),
    )
    await page.locator('[data-testid="day-switch"] button[aria-label="Следующий день"]').click()
    await settle(400)
    // the quick add: the board's order, the tags, the plan tag, the recents
    await goto('/today', '[data-testid="calorie-sticker"]')
    await page.locator('[data-testid="quick-add-button"]').click()
    await page.locator('[role="dialog"]').last().waitFor({ timeout: 8000 })
    await settle(700)
    const sheet = await page.evaluate(() => {
      const dialog = [...document.querySelectorAll('[role="dialog"]')].pop()
      const grid = dialog.querySelector('.sk-qa-grid')
      const tiles = [...grid.children].map((el) => {
        const rect = el.getBoundingClientRect()
        return { name: (el.querySelector('span.text-sm')?.textContent ?? el.textContent ?? '').trim().slice(0, 24), top: Math.round(rect.top), left: Math.round(rect.left), width: Math.round(rect.width) }
      })
      const recents = [...dialog.querySelectorAll('.sk-recent')].map((el) => (el.textContent ?? '').replace(/\s+/g, ' ').trim())
      return { tiles, tags: [...dialog.querySelectorAll('.sk-ai-tag')].length, planTag: dialog.querySelector('.sk-qa-tag')?.textContent ?? null, recents, role: dialog.getAttribute('aria-modal') }
    })
    const order = sheet.tiles.slice().sort((a, b) => a.top - b.top || a.left - b.left).map((tile) => tile.name)
    check(
      order.join('|') === ['Фото', 'Текст', 'Штрих-код', 'Поиск', 'Добавить воду', 'Шаги', 'Начать тренировку', 'Новая задача', 'Итоги дня'].join('|') &&
        sheet.tiles.find((tile) => /Начать/.test(tile.name))?.width > 300 && sheet.tags === 2 && sheet.role === 'true',
      'Наклейки · быстрое добавление: a dialog; the board\'s order (Фото, Текст, Штрих-код, Поиск, вода, шаги, тренировка on its own row, задача, итоги), two «ИИ» tags',
      order.join(' | '),
    )
    ok('Наклейки · быстрое добавление: the plan tag and «Недавнее»', `${sheet.planTag ?? 'no plan today — no tag'}; ${sheet.recents.length} recent(s)`)
    check(sheet.recents.length <= 3 && sheet.recents.every((label) => /^Записать снова: .+\d ккал$/.test(label)), 'Наклейки · быстрое добавление: at most three recents, named «Записать снова: …» with its kcal', sheet.recents.join(' | '))
    await audit('Наклейки · быстрое добавление с «Недавнее» (Сирень)', 'dialog', 's10-16-stickers-add-recents-lilac')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
    // the Plan: priority stickers (the P1 task made above) and the group counts
    await goto('/plan', '[data-testid="plan-today"]')
    const plan = await page.evaluate(() => ({
      prio: [...document.querySelectorAll('[data-testid="task-prio"]')].map((el) => el.textContent?.replace(/\s+/g, ' ').trim()),
      counts: [...document.querySelectorAll('.sk-count')].length,
      date: getComputedStyle(document.querySelector('.sk-datehead')).fontSize,
    }))
    check(
      plan.prio.length > 0 && plan.prio.every((text) => /Приоритет P[123]: P[123]/.test(text)) && plan.counts > 0 && plan.date === '18px',
      'Наклейки · План: priority stickers P1–P3 at the right of a task (named for screen readers), counts as stickers, the day heading 18 px',
      JSON.stringify(plan),
    )
    // Статистика: the plaque's form (medal, week and today stickers, streak sticker, the big bar)
    await goto('/stats', '[data-testid="level-plaque"]')
    const form = await page.evaluate(() => {
      const root = document.querySelector('[data-testid="level-plaque"]')
      const box = (sel) => root.querySelector(sel)?.getBoundingClientRect()
      const card = root.getBoundingClientRect()
      const streak = box('[data-testid="plaque-streak"]')
      const medal = box('.sk-medal')
      const bar = box('[role="progressbar"]')
      return {
        medalOverhang: medal ? Math.round(card.top - medal.top) : null,
        streakCorner: streak ? Math.round(card.right - streak.right) : null,
        streakTop: streak ? Math.round(streak.top - card.top) : null,
        barHeight: bar ? Math.round(bar.height) : null,
        label: root.querySelector('.sk-bar-text')?.textContent?.replace(/\s+/g, ' ') ?? null,
        week: root.querySelector('[data-testid="plaque-week"]')?.textContent?.replace(/\s+/g, ' ').trim(),
        achv: !!document.querySelector('header.sk-header .sk-actions [data-testid="stats-achievements-link"]'),
        flat: getComputedStyle(document.querySelector('.sk-stat') ?? document.body).borderTopWidth,
      }
    })
    check(
      form.medalOverhang > 0 && form.streakCorner <= 14 && form.streakTop <= 10 && form.barHeight === 22 && /\d+ \/ \d+ XP/.test(form.label ?? '') && /за неделю/.test(form.week ?? '') && form.achv && form.flat === '0px',
      'Наклейки · Статистика: the plaque as the board — the medal over the corner, the streak sticker top right, the week\'s XP sticker, a 22 px bar with its numbers, «Достижения» in the header, flat figures',
      JSON.stringify(form),
    )
    // Тренировки: the week strip cell sizes (the board: 58 px), Оформление's header row
    await goto('/train', '[data-testid="week-strip"]')
    const train = await page.evaluate(() => ({
      cells: [...document.querySelectorAll('[data-testid="week-strip"] .sk-day')].map((el) => Math.round(el.getBoundingClientRect().height)),
      list: document.querySelectorAll('[data-testid="plan-exercises"] li').length,
      range: document.querySelector('.sk-week-range')?.textContent ?? null,
    }))
    check(train.cells.length === 7 && train.cells.every((height) => height === 58) && !!train.range, 'Наклейки · Тренировки: seven 58 px day cells and the week\'s date range', JSON.stringify(train))
    ok('Наклейки · Тренировки: the planned workout\'s exercise list', train.list ? `${train.list} exercise(s)` : 'nothing planned today in this database — the list steps aside')
    await goto('/settings/theme', '[data-testid="theme-gallery"]')
    const themeHead = await page.evaluate(() => {
      const back = document.querySelector('.sk-sh .btn-secondary')?.getBoundingClientRect()
      const title = document.querySelector('.sk-settings-title')?.getBoundingClientRect()
      return { back: back ? [Math.round(back.width), Math.round(back.height)] : null, sameRow: !!back && !!title && Math.abs(back.top + back.height / 2 - (title.top + title.height / 2)) < 6, header: !!document.querySelector('header.sk-header [data-testid="sync-pill"]') }
    })
    check(
      themeHead.back?.join('×') === '48×48' && themeHead.sameRow && !themeHead.header,
      'Наклейки · Оформление: the back button (48 px) and the title share one row, as the board',
      JSON.stringify(themeHead),
    )

    await appearance('dark', { skin: 'stickers', ground: 'lilac', tilt: true, shadow: 'hard' })
    await goto('/today', '[data-testid="calorie-sticker"]')
    state = await attrs()
    check(state.theme === 'dark' && state.meta === NIGHT && state.bg === 'rgb(23, 22, 31)', 'Наклейки · Ночь: the night ground #17161F, theme-color too', `${state.bg} ${state.meta}`)
    await audit('Наклейки · Сегодня (Ночь)', null, 's10-20-stickers-today-night')
    await page.locator('[data-testid="quick-add-button"]').click()
    await page.locator('[role="dialog"]').last().waitFor({ timeout: 8000 })
    await settle(600)
    await audit('Наклейки · быстрое добавление (Ночь)', 'dialog', 's10-21-stickers-add-night')
    await page.keyboard.press('Escape')
    await goto('/food', 'input[placeholder*="Поиск"]')
    await audit('Наклейки · Питание (Ночь)', null, 's10-22-stickers-food-night')
    await goto('/stats', '[data-testid="level-plaque"]')
    await page
      .waitForFunction(() => document.querySelectorAll('[data-testid^="stats-section-"]').length === 5 && !document.querySelector('[data-testid^="stats-skeleton-"]'), null, { timeout: 20_000 })
      .catch(() => {})
    await settle()
    await audit('Наклейки · Статистика (Ночь)', null, 's10-23-stickers-stats-night')
    await goto('/train', '[data-testid="week-strip"]')
    await audit('Наклейки · Тренировки (Ночь)', null, 's10-24-stickers-train-night')
    await goto('/plan', '[data-testid="plan-today"]')
    await audit('Наклейки · План (Ночь)', null, 's10-25-stickers-plan-night')
    await goto('/settings/theme', '[data-testid="theme-gallery"]')
    await audit('Оформление (Наклейки, Ночь)', null, 's10-26-theme-stickers-night')

    // «Крупный текст» in both themes: no screen becomes wider than the 390 px screen
    for (const [label, mode, stored] of [
      ['Эволюция', 'dark', { skin: 'evolution', largeText: true }],
      ['Наклейки', 'light', { skin: 'stickers', ground: 'lilac', tilt: true, shadow: 'hard', largeText: true }],
    ]) {
      await appearance(mode, stored)
      const paths = ['/today', '/food', '/train', '/health', '/plan', '/plan/calendar', '/stats', '/settings', '/settings/theme']
      const wide = []
      for (const path of paths) {
        await goto(path, 'main')
        const widened = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)
        if (widened) wide.push(path)
      }
      check(wide.length === 0, `Крупный текст (${label}): no screen is wider than 390 px`, wide.length ? wide.join(' ') : `${paths.length} screens`)
    }
  } catch (error) {
    bad('stage 10 theme checks', String(error?.message ?? error).split('\n')[0])
  } finally {
    await page.emulateMedia({ colorScheme: null }).catch(() => {})
    try {
      writeFileSync(`${SHOTS}/s10-axe.json`, JSON.stringify({ totals, report }, null, 2))
    } catch {
      /* the screenshots directory is optional */
    }
    ok('axe summary (stage 10)', `${totals.audits} audits: ${totals.blocking} serious/critical, ${totals.moderate} moderate, ${totals.minor} minor node(s) — ${SHOTS}/s10-axe.json`)
    check(errors.length === 0, 'no console errors during the theme checks', errors.slice(0, 2).join(' | '))
    // leave the device as the other modules expect it: «Эволюция», dark; remove the day's rows
    await appearance('dark', null).catch(() => {})
    const removed = []
    for (const id of created.meals) removed.push((await apiFetch(`/v1/nutrition/meals/${id}`, { method: 'DELETE' }).catch(() => ({ status: 0 }))).status)
    for (const id of created.tasks) removed.push((await apiFetch(`/v1/planner/tasks/${id}`, { method: 'DELETE' }).catch(() => ({ status: 0 }))).status)
    for (const id of created.water) removed.push((await apiFetch(`/v1/nutrition/water/${id}`, { method: 'DELETE' }).catch(() => ({ status: 0 }))).status)
    if (created.steps) removed.push((await apiFetch(`/v1/health/steps/${created.steps}`, { method: 'DELETE' }).catch(() => ({ status: 0 }))).status)
    const reset = await page.evaluate(() => [window.localStorage.getItem('dsos.theme'), window.localStorage.getItem('dsos.appearance')]).catch(() => ['?', '?'])
    check(
      removed.every((status) => status === 200 || status === 204) && reset[0] === 'dark' && reset[1] === null,
      'theme test data removed, the appearance back to «Эволюция» (dark)',
      `${removed.length} rows deleted; ${reset.join(' / ')}`,
    )
    await page.close().catch(() => {})
    await ctx.page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' }).catch(() => {})
  }
}
