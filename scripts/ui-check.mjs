#!/usr/bin/env node
/**
 * Browser acceptance check (stages 1–3).
 *
 * Drives a real Chromium (Playwright) through the whole pairing → onboarding →
 * today → settings flow against a running stack, and asserts the things a
 * smoke test against the HTTP API cannot see: the app shell renders, the
 * wizard advances, i18n switches live, the theme toggles, the quick-add sheet
 * opens, and no console errors / failed requests escape.
 *
 * Usage:
 *   PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs
 *   PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs PAIRING_CODE=… node scripts/ui-check.mjs
 *   WEB_URL=http://127.0.0.1:5173 SHOTS=/tmp/shots node scripts/ui-check.mjs
 *
 * The code must be freshly issued (single use, 15 min TTL):
 *   docker compose exec api python -m app.cli pairing-code
 */
// `playwright` is an optional dev tool: install it anywhere and point
// PLAYWRIGHT_MODULE at it, or just `npm i -D playwright` in the repo root.
const { chromium, devices } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright')
const { existsSync, readFileSync } = await import('node:fs')
const { dirname, join, resolve } = await import('node:path')
const { fileURLToPath } = await import('node:url')

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

const WEB_URL = process.env.WEB_URL ?? 'http://127.0.0.1:5173'
const CODE = process.env.PAIRING_CODE ?? ''
const SHOTS = process.env.SHOTS ?? '/tmp/dsos-shots'
const iPhone = devices['iPhone 13']

if (!CODE) {
  console.error('PAIRING_CODE is required (python -m app.cli pairing-code)')
  process.exit(2)
}

const failures = []
const consoleErrors = []
const requestErrors = []
const ok = (label, detail = '') => console.log(`  ok   ${label}${detail ? ` — ${detail}` : ''}`)
const bad = (label, detail = '') => {
  failures.push(`${label}${detail ? ` — ${detail}` : ''}`)
  console.log(`  FAIL ${label}${detail ? ` — ${detail}` : ''}`)
}
const check = (condition, label, detail = '') => (condition ? ok(label, detail) : bad(label, detail))

// Stage 3: FAKE_CAMERA=/tmp/barcode.y4m (scripts/make-barcode-y4m.py) makes the
// barcode scanner see a real EAN-13 through Chromium's fake camera.
const FAKE_CAMERA = process.env.FAKE_CAMERA && existsSync(process.env.FAKE_CAMERA) ? process.env.FAKE_CAMERA : ''
const BARCODE = process.env.BARCODE ?? '4607001234562'
// CHROMIUM_NO_DEV_SHM=1: Chromium keeps its shared memory in /tmp instead of /dev/shm (where a
// container counts it against the memory limit — the dev server's pages got it OOM-killed)
const browser = await chromium.launch({
  args: [
    ...(process.env.CHROMIUM_NO_DEV_SHM ? ['--disable-dev-shm-usage'] : []),
    ...(FAKE_CAMERA
      ? [
          '--use-fake-ui-for-media-stream',
          '--use-fake-device-for-media-stream',
          `--use-file-for-fake-video-capture=${FAKE_CAMERA}`,
        ]
      : []),
  ],
})
const context = await browser.newContext({
  ...iPhone,
  viewport: { width: 390, height: 844 },
  locale: 'ru-RU',
  timezoneId: 'Europe/Moscow',
  geolocation: undefined,
  permissions: FAKE_CAMERA ? ['geolocation', 'camera'] : ['geolocation'],
})
// stage 10: «Одинаково на всех устройствах» is on by default. The earlier modules set the look through
// localStorage and expect it to stay on this device, so this browser starts with the switch off (only
// when it has never been set); scripts/ui-checks/stage10-appearance-sync.mjs opens browsers of its own.
await context.addInitScript(() => {
  if (window.localStorage.getItem('dsos.appearance.sync') === null) window.localStorage.setItem('dsos.appearance.sync', 'off')
})
const page = await context.newPage()

let offlineMode = false // stage 3 offline check: failures while offline are expected
page.on('console', (message) => {
  if (message.type() === 'error' && !offlineMode) consoleErrors.push(message.text())
})
page.on('pageerror', (error) => consoleErrors.push(String(error)))
page.on('requestfailed', (request) => {
  if (offlineMode) return
  const url = request.url()
  // Navigations abort in-flight requests; that is not an app defect.
  if (url.includes('/api/') && !/ERR_ABORTED/i.test(request.failure()?.errorText ?? '')) {
    requestErrors.push(`${request.method()} ${url} (${request.failure()?.errorText})`)
  }
})
page.on('response', (response) => {
  // a 5xx a check provokes on purpose (stage 9: a failing server, inside `allowErrors`) is not an app error
  if (response.url().includes('/api/') && response.status() >= 500 && !offlineMode) {
    requestErrors.push(`${response.status()} ${response.url()}`)
  }
})

const shot = async (name) => {
  await page.screenshot({ path: `${SHOTS}/${name}.png` })
}
/** Inner text with every Unicode space normalised (ru-RU formats 1 780 with U+00A0). */
const text = async () => (await page.locator('body').innerText()).replace(/[\u00A0\u202F\u2009\u200B]/g, ' ')
/** Case-insensitive: `.label` is rendered uppercase by CSS text-transform. */
const hasText = async (needle) => (await text()).toLowerCase().includes(needle.toLowerCase())

try {
  // The context is brand new, so origin storage is already empty — do NOT clear
  // it in an init script: that would also wipe the device token on every reload.

  // 1. Unauthenticated visit must show only the pairing screen.
  console.log('\n1. pairing gate')
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  check(
    (await text()).includes('Подключение устройства'),
    'no token → pairing screen only',
    '(no app shell leaked)',
  )
  check(
    (await page.locator('nav.tabbar').count()) === 0,
    'bottom tab bar is not rendered before pairing',
  )
  await shot('01-pairing')

  // 2. Pair with the one-time code.
  console.log('\n2. pairing')
  await page.locator('#pairing-code').fill(CODE)
  await page.locator('#pairing-code').blur()
  await page.getByRole('button', { name: 'Подключить' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1500)
  const unlocked = !(await text()).includes('Подключение устройства')
  if (unlocked) {
    ok('pairing consumed the code → app unlocked')
  } else {
    const reason = (await page.locator('[role="alert"], .callout').first().innerText().catch(() => '')).trim()
    bad('pairing consumed the code → app unlocked', reason || 'still on the pairing screen')
  }
  await shot('02-after-pairing')

  // 3. Walk the four wizard steps.
  console.log('\n3. onboarding wizard')
  const WIZARD = 'Настроим расчёты'
  if (!(await text()).includes(WIZARD)) {
    // This owner was already onboarded (e.g. by scripts/smoke.sh). Reopen the
    // wizard through the public settings API so the flow is exercised every run.
    const reset = await page.evaluate(async () => {
      const token = window.localStorage.getItem('dsos.device_token')
      const response = await fetch('/api/v1/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ onboarding_completed: false }),
      })
      return response.status
    })
    check(reset === 200, 'PATCH /settings can reopen onboarding', `HTTP ${reset}`)
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)
  }
  check((await text()).includes(WIZARD), 'onboarding wizard gates first use')
  if ((await text()).includes(WIZARD)) {
    // Step 1 — profile: sex, birth date, activity level (all translated labels).
    check(await hasText('О вас'), 'step 1 is named in the progress bar')
    await page.getByRole('tab', { name: 'Мужской', exact: true }).click()
    await page.locator('#birth').fill('1996-05-04')
    await page.selectOption('#activity', 'light')
    check(
      (await page.locator('#activity').innerText().catch(() => '')) !== '' ||
        (await page.inputValue('#activity')) === 'light',
      'activity select holds a translated level',
      await page.locator('#activity :checked').innerText().catch(() => '?'),
    )
    await page.getByRole('button', { name: 'Далее' }).click()
    await page.waitForTimeout(500)

    // Step 2 — body parameters.
    check(await hasText('Тело'), 'step 2 is named in the progress bar')
    await page.locator('#height').fill('180')
    await page.locator('#weight').fill('80')
    await page.getByRole('button', { name: 'Далее' }).click()
    await page.waitForTimeout(500)

    // Step 3 — goal + live target preview.
    check(await hasText('Цель'), 'step 3 is named in the progress bar')
    await page.getByRole('tab', { name: 'Дефицит', exact: true }).click()
    await page.getByRole('tab', { name: '15%', exact: true }).click()
    await page.getByRole('button', { name: 'Что получится' }).click()
    await page.waitForTimeout(1200)
    const preview = await text()
    check(/1 ?780/.test(preview), 'preview shows BMR = 1 780 kcal (Mifflin, male 30/180/80)')
    check(/2 ?448/.test(preview), 'preview shows TDEE = 2 448 kcal (light ×1.375)')
    check(/2 ?080/.test(preview), 'preview shows target = 2 080 kcal (deficit 15%)')
    check(/1 ?500/.test(preview), 'preview shows the 1 500 kcal safe floor')
    await shot('03-onboarding-preview')
    await page.getByRole('button', { name: 'Далее' }).click()
    await page.waitForTimeout(400)

    // Step 4 — limits & notifications, then save.
    check(await hasText('Ограничения'), 'step 4 is named in the progress bar')
    await page.getByRole('button', { name: 'Сохранить и продолжить' }).click()
    await page.waitForLoadState('networkidle')
    // the app shell renders once the saved settings are back (slow on a busy machine)
    await page.locator('nav.tabbar').waitFor({ timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(300)
  }
  check(await hasText('Сегодня'), 'wizard saved → Today page')
  await shot('04-today')

  // 4. App shell: five tabs + central quick add.
  console.log('\n4. app shell')
  const tabs = await page.locator('nav.tabbar a, nav.tabbar button').count()
  check(tabs >= 5, 'bottom tab bar has ≥ 5 controls', `${tabs} found`)
  await page.locator('nav.tabbar button[aria-label]').first().click()
  await page.waitForTimeout(700)
  const sheet = await text()
  check(sheet.includes('Быстрое добавление'), 'quick-add sheet opens from the central button')
  const sheetActions = await page.locator('[role="dialog"] button, [role="dialog"] a').count()
  check(sheetActions >= 4, 'quick-add sheet lists the stage actions', `${sheetActions} actions`)
  await shot('05-quick-add')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)

  // 5. Settings: language, timezone, theme.
  console.log('\n5. settings')
  await page.goto(`${WEB_URL}/settings`, { waitUntil: 'networkidle' })
  check(await hasText('Настройки'), 'settings hub renders')
  await shot('06-settings')

  await page.goto(`${WEB_URL}/settings/appearance`, { waitUntil: 'networkidle' })
  const appearanceBefore = await text()
  check(
    appearanceBefore.toLowerCase().includes('язык') && appearanceBefore.toLowerCase().includes('тема'),
    'appearance section (RU)',
  )
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  check(bg !== 'rgb(0, 0, 0)', 'dark theme background is not pure black', bg)
  const darkFg = await page.evaluate(() => getComputedStyle(document.body).color)
  ok('dark theme text/background pair', `${darkFg} on ${bg}`)
  check(/Europe\/Moscow/.test(appearanceBefore), 'timezone detected from the device', 'Europe/Moscow')
  await page.getByRole('tab', { name: 'English', exact: true }).click()
  await page.waitForTimeout(1200)
  const appearanceAfter = await text()
  check(
    appearanceAfter.toLowerCase().includes('language') && appearanceAfter.toLowerCase().includes('theme'),
    'language switches to English without a reload',
  )
  await shot('07-settings-english')
  await page.getByRole('tab', { name: 'Русский', exact: true }).click()
  await page.waitForTimeout(1000)
  check(await hasText('Язык'), 'language switches back to Russian')

  // Theme toggle: the root class/attribute must change.
  const themeBefore = await page.evaluate(
    () => document.documentElement.dataset.theme ?? document.documentElement.className,
  )
  await page.getByRole('tab', { name: 'Светлая', exact: true }).click()
  await page.waitForTimeout(700)
  const themeAfter = await page.evaluate(
    () => document.documentElement.dataset.theme ?? document.documentElement.className,
  )
  check(themeBefore !== themeAfter, 'theme switches dark → light', `${themeBefore} → ${themeAfter}`)
  const lightBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  check(lightBg !== bg && lightBg !== 'rgb(0, 0, 0)', 'light theme repaints the background', lightBg)
  await shot('07b-theme-light')
  await page.getByRole('tab', { name: 'Тёмная', exact: true }).click()
  await page.waitForTimeout(600)

  const backToDark = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  check(backToDark === bg, 'theme switches back to dark', backToDark)

  // 6. Devices list shows the just-paired device.
  console.log('\n6. devices & tokens')
  await page.goto(`${WEB_URL}/settings/devices`, { waitUntil: 'networkidle' })
  const devicesText = await text()
  check(/iPhone|Safari|Устройств/i.test(devicesText), 'devices section lists the paired device')
  await shot('08-devices')

  await page.goto(`${WEB_URL}/settings/tokens`, { waitUntil: 'networkidle' })
  check((await hasText('ingest:health')) || (await hasText('токен')), 'tokens section renders')
  await shot('09-tokens')

  // 7. Every tab is reachable and renders (no crash, no blank route).
  console.log('\n7. routes')
  for (const route of ['/today', '/food', '/train', '/plan', '/stats']) {
    await page.goto(`${WEB_URL}${route}`, { waitUntil: 'networkidle' })
    const body = (await text()).trim()
    check(body.length > 40, `${route} renders content`, `${body.length} chars`)
    await shot(`10-route-${route.slice(1)}`)
  }

  // 7½. stage-2 nutrition flow: search → log → dashboard
  console.log('\n7½. nutrition flow')
  await page.goto(`${WEB_URL}/food`, { waitUntil: 'networkidle' })
  const searchBox = page.locator('input[placeholder*="Поиск"], input[placeholder*="Search"]').first()
  await searchBox.fill('гречка отварная')
  await page.waitForTimeout(900) // debounce + query
  const firstResult = page.locator('section div.surface-sunken > button').first()
  await firstResult.click()
  await page.waitForTimeout(600)
  check(await hasText('Граммы'), 'log sheet opens from a search result')
  await shot('11-log-sheet')
  await page.getByRole('button', { name: 'Записать в дневник' }).click()
  await page.waitForTimeout(1200)
  check(await hasText('записано'), 'save shows the confirmation toast')

  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  const todayText = await text()
  // the sheet pre-fills the food's default serving (150 g → 165 kcal)
  check(/165/.test(todayText), 'Today ring shows the logged 165 kcal (150 g buckwheat)')
  // the log sheet picks the type from the browser's clock (Moscow here), so after 22:00 it is a snack
  check(
    (await hasText('Завтрак')) || (await hasText('Обед')) || (await hasText('Ужин')) || (await hasText('Перекус')),
    'meal appears under its type',
  )
  await shot('12-today-with-meal')

  // water quick-add from the dashboard
  const waterRow = page.locator('span.tnum.muted').filter({ hasText: 'мл' }).first()
  const waterBefore = await waterRow.innerText()
  await page.getByRole('button', { name: /\+250 мл/ }).click()
  await page.waitForTimeout(1200)
  const waterAfter = await waterRow.innerText()
  check(waterBefore !== waterAfter, 'water total grows after +250 ml', `${waterBefore} → ${waterAfter}`)
  check(await hasText('мл воды'), 'water toast confirms +250 ml')

  // recipe builder is reachable and validates empty input
  await page.goto(`${WEB_URL}/food?action=recipe`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  check(await hasText('Новый рецепт'), 'recipe sheet opens from the quick-add deep link')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)

  // 7¾. stage 2b: confirm an unverified food, recipe yield/portion, pinned on Today
  console.log('\n7¾. stage 2b backfill')
  await page.goto(`${WEB_URL}/food`, { waitUntil: 'networkidle' })
  await searchBox.fill('борщ')
  await page.waitForTimeout(900)
  const borschtRow = page.locator('section div.surface-sunken').filter({ hasText: 'Борщ с мясом' }).first()
  check((await borschtRow.count()) === 1, 'new seed dish is searchable in Russian', 'Борщ с мясом')
  const tookText = (await page.locator('p.text-right.muted').first().innerText().catch(() => '')).trim()
  const tookMs = Number((tookText.match(/(\d+)\s*ms/) ?? [])[1] ?? NaN)
  check(tookMs < 150, 'search took_ms under the 150 ms budget', `${tookMs} ms`)
  const confirmChip = borschtRow.getByRole('button', { name: /Подтвердить/ })
  const wasUnverified = (await confirmChip.count()) === 1
  if (wasUnverified) {
    await shot('13-verify-chip')
    await confirmChip.click()
    await page.waitForTimeout(1000)
    check(await hasText('Данные подтверждены'), 'one tap confirms the food (toast)')
    check((await borschtRow.getByRole('button', { name: /Подтвердить/ }).count()) === 0, 'confirm chip disappears after confirming')
  } else {
    ok('food was confirmed by an earlier run — chip correctly absent')
  }

  await searchBox.fill('сырники')
  await page.waitForTimeout(900)
  await page.locator('section div.surface-sunken > button').first().click()
  await page.waitForTimeout(600)
  check(await hasText('не проверены'), 'log sheet explains an unverified food and offers confirm')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)

  // recipe with an explicit cooked weight and a portion defined in grams, pinned
  const recipeName = `Каша UI ${Date.now() % 100000}`
  await page.goto(`${WEB_URL}/food?action=recipe`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  await page.locator('#recipe-name').fill(recipeName)
  await page.getByRole('button', { name: 'Добавить ингредиент' }).click()
  await page.locator('[role="dialog"] input[placeholder*="Поиск"]').fill('гречка (ядрица')
  await page.waitForTimeout(1000)
  await page.locator('[role="dialog"] .max-h-56 button').first().click()
  await page.waitForTimeout(300)
  await page.locator('[role="dialog"] input[aria-label="Граммы"]').first().fill('100')
  await page.locator('#recipe-cooked').fill('300')
  await page.getByRole('tab', { name: 'Вес порции', exact: true }).click()
  await page.locator('#recipe-portion').fill('150')
  await page.waitForTimeout(300)
  const recipePreview = await text()
  // 100 g dry buckwheat = 343 kcal → 300 g cooked → 150 g portion = 172 kcal (rounded)
  check(/172 ккал в порции 150 г/.test(recipePreview), 'recipe preview: per-portion kcal from the cooked weight', '172 kcal / 150 g')
  check(/114 ккал на 100 г готового/.test(recipePreview), 'recipe preview: per 100 g of the cooked dish', '114 kcal')
  await page.getByRole('switch', { name: 'Закрепить на «Сегодня»' }).click()
  await shot('14-recipe-sheet')
  await page.getByRole('button', { name: 'Сохранить рецепт' }).click()
  await page.waitForTimeout(1200)
  check(await hasText(recipeName), 'recipe saved and listed')

  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  const pinnedChip = page.locator('section[aria-label="Закреплённые рецепты"] button').filter({ hasText: recipeName })
  check((await pinnedChip.count()) === 1, 'pinned recipe shows on Today')
  await pinnedChip.scrollIntoViewIfNeeded()
  await page.evaluate(() => window.scrollBy(0, 160))
  await shot('15-today-pinned')
  const dayKcal = () =>
    page.evaluate(async () => {
      const token = window.localStorage.getItem('dsos.device_token')
      const day = await fetch('/api/v1/nutrition/day', { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json())
      return day.totals.kcal
    })
  const kcalBefore = await dayKcal()
  await pinnedChip.click()
  await page.waitForTimeout(600)
  check(await hasText('1 порц. · 150 г'), 'log sheet opens at one portion of the recipe')
  await page.getByRole('button', { name: 'Записать в дневник' }).click()
  await page.waitForTimeout(1200)
  const kcalAfter = await dayKcal()
  check(Math.abs(kcalAfter - kcalBefore - 171.5) < 1, 'logging a pinned portion adds its kcal to the day', `${kcalBefore} → ${kcalAfter}`)

  // clean up: unpin so the next run starts with a single pinned recipe of its own
  await page.goto(`${WEB_URL}/food`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  await page.getByRole('button', { name: 'Открепить' }).first().click().catch(() => {})
  await page.waitForTimeout(600)

  // barcode API: invalid codes are answered, never sent to Open Food Facts
  const barcode = await page.evaluate(async () => {
    const token = window.localStorage.getItem('dsos.device_token')
    const response = await fetch('/api/v1/foods/barcode/not-a-code', { headers: { Authorization: `Bearer ${token}` } })
    return response.json()
  })
  check(barcode.lookup === 'invalid', 'barcode endpoint reports its lookup outcome', barcode.lookup)

  // 7⅞. stage 3: AI logging (fake provider on the dev API) and the barcode scanner
  console.log('\n7⅞. stage 3 AI logging & barcode')
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
  const mealCount = async () => (await apiFetch('/v1/nutrition/day')).body?.logged_meals ?? -1
  const openQuickAdd = async (tile) => {
    await page.locator('nav.tabbar button[aria-label]').first().click()
    await page.waitForTimeout(500)
    await page.locator('[role="dialog"]').getByText(tile, { exact: true }).click()
    await page.waitForTimeout(500)
  }

  const config = (await apiFetch('/v1/meta/app-config')).body
  check(config?.features?.ai_meal_text === true, 'AI features are on (fake provider)', config?.ai?.provider ?? 'off')

  // text → review → edit → save
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  const mealsBefore = await mealCount()
  await openQuickAdd('Текст')
  check(await hasText('Что вы съели?'), 'quick add → text sheet in one tap')
  await page.locator('#ai-text').fill('200 г гречки, 2 яйца и куриная грудка 150 g')
  await page.getByRole('button', { name: 'Распознать' }).click()
  await page.getByText('Проверьте запись').waitFor({ timeout: 15000 })
  const reviewItems = page.locator('[data-testid="ai-item"]')
  check((await reviewItems.count()) === 3, 'review lists the three recognised foods', `${await reviewItems.count()} items`)
  check(await hasText('≈ 200 г — оценка'), 'grams are shown as an estimate')
  check(await hasText('из базы'), 'items are matched to the local library')
  check((await mealCount()) === mealsBefore, 'nothing is saved before confirming (no auto-save)')
  await reviewItems.nth(0).locator('input[type="number"]').fill('150')
  await page.waitForTimeout(200)
  check(/165 ккал/.test(await reviewItems.nth(0).innerText()), 'editing grams recomputes kcal (150 g buckwheat = 165)')
  await reviewItems.nth(1).getByRole('button', { name: 'Заменить продукт' }).click()
  await page.waitForTimeout(400)
  const swapTarget = reviewItems.nth(1).locator('.surface-sunken button').filter({ hasText: 'Яйцо варёное' }).first()
  if (await swapTarget.count()) {
    await swapTarget.click()
    await page.waitForTimeout(300)
    check((await reviewItems.nth(1).innerText()).includes('Яйцо варёное'), 'swap product from the alternatives')
  } else {
    bad('swap product from the alternatives', 'Яйцо варёное not offered')
  }
  const removeBox = await reviewItems.nth(2).getByRole('button', { name: /^Убрать/ }).boundingBox()
  check(
    !!removeBox && removeBox.width >= 44 && removeBox.height >= 44,
    'remove button is a full tap target',
    removeBox ? `${Math.round(removeBox.width)}×${Math.round(removeBox.height)}` : 'missing',
  )
  await reviewItems.nth(2).getByRole('button', { name: /^Убрать/ }).click()
  await page.waitForTimeout(300)
  check((await reviewItems.count()) === 2, 'remove an item before saving')
  // a hand-added item: its slider range stays put while dragging
  await page.getByRole('button', { name: 'Добавить продукт' }).click()
  const pickerClose = await page.locator('.surface-sunken').getByRole('button', { name: 'Отмена' }).boundingBox()
  check(!!pickerClose && pickerClose.width >= 44 && pickerClose.height >= 44, 'picker close is a full tap target')
  await page.locator('.surface-sunken input').fill('банан')
  const bananaPick = page.locator('.surface-sunken button').filter({ hasText: 'Банан' }).first()
  await bananaPick.waitFor({ timeout: 5000 }).catch(() => {})
  if (await bananaPick.count()) {
    await bananaPick.click()
    await page.waitForTimeout(300)
    const added = reviewItems.nth(2)
    const slider = added.locator('input[type="range"]')
    const maxBefore = await slider.getAttribute('max')
    await slider.fill(maxBefore ?? '300')
    await page.waitForTimeout(150)
    const maxAfter = await slider.getAttribute('max')
    check(
      maxBefore === maxAfter && (await added.locator('input[type="number"]').inputValue()) === maxBefore,
      'slider range is anchored (dragging to the end does not rescale it)',
      `max ${maxBefore} → ${maxAfter}`,
    )
    await added.getByRole('button', { name: /^Убрать/ }).click()
    await page.waitForTimeout(300)
  } else {
    bad('slider range is anchored (dragging to the end does not rescale it)', 'Банан not found in the picker')
  }
  await shot('16-ai-review')
  await page.getByRole('button', { name: /^Записать ·/ }).click()
  await page.waitForTimeout(1200)
  check(await hasText('Записано: 2 поз.'), 'explicit save shows the toast')
  check((await mealCount()) === mealsBefore + 1, 'one meal saved after confirmation')
  const day = (await apiFetch('/v1/nutrition/day')).body
  const textMeal = (day?.meals ?? []).find((meal) => meal.source === 'text')
  check(
    !!textMeal && textMeal.items.every((item) => item.confidence !== null) && textMeal.items.some((item) => item.is_edited),
    'saved items keep confidence and the edited flag',
  )

  // photo (+ opted-in 400 px thumbnail) → review → save → thumbnail on Today
  const keep = await apiFetch('/v1/settings', { method: 'PATCH', body: JSON.stringify({ keep_photo_thumbnail: true }) })
  check(keep.status === 200, 'enable "keep photo thumbnail"')
  await page.reload({ waitUntil: 'networkidle' })
  const jpegPhoto = await page.screenshot({ type: 'jpeg', quality: 70 })
  await page.setInputFiles('#ai-photo-input', { name: 'meal.jpg', mimeType: 'image/jpeg', buffer: jpegPhoto })
  await page.getByText('Проверьте фото-запись').waitFor({ timeout: 15000 })
  check((await reviewItems.count()) === 3, 'photo → downscaled JPEG → three suggestions')
  check(await hasText('не видны'), 'the model note is shown in the UI language (hidden oil/sauce)')
  await shot('17-ai-photo-review')
  await page.getByRole('button', { name: /^Записать ·/ }).click()
  await page.waitForTimeout(1500)
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const thumb = page.locator('img[alt="Фото приёма пищи"]').first()
  check((await thumb.count()) === 1 && (await thumb.evaluate((img) => img.naturalWidth)) > 0, 'the kept thumbnail shows on Today')
  await thumb.scrollIntoViewIfNeeded().catch(() => {})
  await shot('18-today-photo-meal')
  await apiFetch('/v1/settings', { method: 'PATCH', body: JSON.stringify({ keep_photo_thumbnail: false }) })

  // offline: a clear message, no request
  offlineMode = true
  await context.setOffline(true)
  await openQuickAdd('Текст')
  await page.locator('#ai-text').fill('банан')
  await page.getByRole('button', { name: 'Распознать' }).click()
  await page.waitForTimeout(600)
  check(await hasText('ИИ-функции доступны только онлайн'), 'offline: AI shows a clear message')
  await page.locator('[role="dialog"]').getByRole('button', { name: /^(Закрыть|Close)$/ }).click()
  await page.waitForTimeout(800)
  // the photo tile says so before the camera opens (no picture taken in vain)
  let chooserOpened = false
  const onChooser = () => {
    chooserOpened = true
  }
  // let the earlier offline message go, so the check below sees a fresh one
  await page.getByText('ИИ-функции доступны только онлайн').first().waitFor({ state: 'detached', timeout: 8000 }).catch(() => {})
  page.on('filechooser', onChooser)
  await page.locator('nav.tabbar button[aria-label]').first().click()
  await page.waitForTimeout(500)
  await page.locator('[role="dialog"]').getByText('Фото', { exact: true }).click()
  await page.waitForTimeout(700)
  page.off('filechooser', onChooser)
  check(!chooserOpened && (await hasText('ИИ-функции доступны только онлайн')), 'offline: photo tile warns before opening the camera')
  await page.locator('[role="dialog"]').getByRole('button', { name: /^(Закрыть|Close)$/ }).click()
  await context.setOffline(false)
  await page.waitForTimeout(800)
  offlineMode = false

  // barcode: a product with this EAN must exist (created once as "my food")
  const known = await apiFetch(`/v1/foods/barcode/${BARCODE}?live=false`)
  if (!known.body?.food) {
    await apiFetch('/v1/foods', {
      method: 'POST',
      body: JSON.stringify({ name_ru: 'Кефир сканер UI', kcal_per_100g: 52, protein_per_100g: 3, barcode: BARCODE }),
    })
  }
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await openQuickAdd('Штрих-код')
  if (FAKE_CAMERA) {
    // the real zxing decoder reads the EAN-13 from the fake camera feed
    const found = await page
      .getByText('Записать в дневник')
      .waitFor({ timeout: 15000 })
      .then(() => true)
      .catch(() => false)
    check(found, 'camera scan (zxing) → product → log sheet', BARCODE)
  } else {
    await page.getByText('Сканер штрихкода').waitFor({ timeout: 8000 })
    await page.locator('#barcode-manual').fill(BARCODE)
    await page.getByRole('button', { name: 'Найти' }).click()
    await page.getByText('Записать в дневник').waitFor({ timeout: 8000 })
    ok('typed barcode → product → log sheet (no fake camera in this run)')
  }
  check(await hasText('Кефир сканер UI'), 'the scanned product opens in the log sheet')
  const otherTab = await page.getByRole('tab', { name: 'Другое' }).boundingBox()
  check(!!otherTab && otherTab.x + otherTab.width <= 390, 'five meal types fit a 390 px screen', `${Math.round((otherTab?.x ?? 0) + (otherTab?.width ?? 0))} px`)
  await shot('19-barcode-log-sheet')
  await page.getByRole('button', { name: 'Записать в дневник' }).click()
  await page.waitForTimeout(1200)
  const barcodeMeal = ((await apiFetch('/v1/nutrition/day')).body?.meals ?? []).find((meal) => meal.source === 'barcode')
  check(!!barcodeMeal && barcodeMeal.items[0]?.match_method === 'barcode', 'barcode meal saved with source=barcode')

  // 7⁹. stage 4: training — template, weekly plan, live workout, rest timer, finish, cardio
  console.log('\n7⁹. stage 4 training')
  const tag = String(Date.now()).slice(-4)
  const left = (await apiFetch('/v1/training/today')).body
  if (left?.active) await apiFetch(`/v1/training/workouts/${left.active.id}`, { method: 'DELETE' })
  const library = (await apiFetch('/v1/training/exercises?limit=200')).body?.items ?? []
  check(library.length >= 52, 'exercise library is seeded', `${library.length} exercises`)

  await page.goto(`${WEB_URL}/train`, { waitUntil: 'networkidle' })
  check(await hasText('Сожжено за 7 дней'), 'Train tab shows burned kcal in its own card')
  check(await hasText('не прибавляется к бюджету'), 'the burned card says it is not food budget')

  // template through the UI
  await page.getByRole('button', { name: 'Новый шаблон' }).first().click()
  await page.locator('#template-title').fill(`UI Грудь ${tag}`)
  await page.getByRole('button', { name: 'Добавить упражнение' }).click()
  await page.locator('#exercise-search').fill('жим лёжа')
  await page.waitForTimeout(900)
  const firstHit = (await page.locator('[data-testid="exercise-results"] button').first().innerText()).split('\n')[0]
  check(firstHit === 'Жим штанги лёжа', 'exercise search: «жим лёжа» → «Жим штанги лёжа» first', firstHit)
  await page.locator('[data-testid="exercise-results"] button').first().click()
  await page.waitForTimeout(500)
  await page.locator('[data-testid="template-item"] input').nth(2).fill('60')
  await shot('23-template-editor')
  await page.getByRole('button', { name: 'Сохранить' }).click()
  await page.waitForTimeout(900)
  check(await hasText(`UI Грудь ${tag}`), 'template saved and listed')

  // weekly plan: today's cell → the template
  await page.locator('[data-testid="week-strip"] button.border-aqua-400\\/50').click()
  await page.waitForTimeout(500)
  await page.locator('[data-testid="plan-sheet"] button').filter({ hasText: `UI Грудь ${tag}` }).click()
  await page.waitForTimeout(900)
  check(await hasText(`UI Грудь ${tag}`) && (await hasText('Начать по плану')), 'planned for today → «Начать по плану»')
  await page.waitForTimeout(3400) // toasts gone
  await page.evaluate(() => window.scrollTo(0, 0))
  await shot('20-train-page')

  // live workout
  await page.getByRole('button', { name: 'Начать по плану' }).click()
  await page.waitForURL(/\/train\/workout\//, { timeout: 8000 })
  const workoutId = page.url().split('/').pop()
  const card = page.locator('[data-testid="workout-exercise"]').first()
  // the three planned rows render once the workout is loaded (slow on a busy machine)
  await card.locator('input[inputmode="decimal"]').nth(2).waitFor({ timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(300)
  const weights = await card.locator('input[inputmode="decimal"]').evaluateAll((els) => els.map((el) => el.value))
  // rows come from the last workout with this exercise, else from the template target (60 kg)
  const started = (await apiFetch(`/v1/training/workouts/${workoutId}`)).body
  const prev = (started?.exercises?.[0]?.previous ?? []).filter((set) => !set.is_warmup)
  const expected = [0, 1, 2].map((i) => String(prev[i]?.weight_kg ?? prev[prev.length - 1]?.weight_kg ?? 60).replace('.', ','))
  check(
    weights.length === 3 && weights.every((value, i) => value === expected[i]),
    'three planned sets pre-filled (last time, else the template target)',
    `${weights.join(',')} — ${prev.length ? 'from last time' : 'from the template'}`,
  )
  await card.locator('[data-testid="save-set"]').nth(0).click()
  await page.waitForTimeout(900)
  check(await page.locator('[data-testid="rest-timer"]').isVisible(), 'logging a set starts the rest timer')
  await card.locator('input[inputmode="decimal"]').nth(1).fill('62,5')
  await card.locator('[data-testid="save-set"]').nth(1).click()
  await page.waitForTimeout(900)
  await shot('21-workout-live')
  let live = (await apiFetch(`/v1/training/workouts/${workoutId}`)).body
  check(live?.total_sets === 2 && live?.exercises?.[0]?.sets?.[1]?.weight_kg === 62.5, 'sets saved (comma decimals: 62,5 kg)', `${live?.total_sets} sets`)
  await page.getByRole('button', { name: 'Дальше' }).click()

  // an exercise added on the fly: pull-ups (reps only)
  await page.getByRole('button', { name: 'Добавить упражнение' }).click()
  await page.locator('#exercise-search').fill('подтягивания')
  await page.waitForTimeout(900)
  await page.locator('[data-testid="exercise-results"] button').first().click()
  await page.waitForTimeout(500)
  const pull = page.locator('[data-testid="workout-exercise"]').last()
  await pull.locator('input[inputmode="numeric"]').first().fill('9')

  // offline: the set is queued and replayed when the network is back
  offlineMode = true
  await context.setOffline(true)
  await pull.locator('[data-testid="save-set"]').first().click()
  await page.waitForTimeout(700)
  check(await hasText('Нет сети — подход сохранится'), 'offline set is queued with a clear message')
  await context.setOffline(false)
  offlineMode = false
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await page.waitForTimeout(2500)
  live = (await apiFetch(`/v1/training/workouts/${workoutId}`)).body
  check(live?.total_sets === 3, 'the queued set reached the server after reconnect', `${live?.total_sets} sets`)

  // finish → summary
  await page.getByRole('button', { name: 'Завершить', exact: true }).click()
  await page.locator('#finish-minutes').fill('35')
  await page.getByRole('radio', { name: '4' }).click()
  await page.getByRole('button', { name: 'Завершить тренировку' }).click()
  await page.locator('[data-testid="workout-summary"]').waitFor({ timeout: 8000 })
  const summary = await page.locator('[data-testid="workout-summary"]').innerText()
  check(/\d+\s?ккал/.test(summary) && summary.includes('не прибавляется'), 'summary: burned kcal shown apart from the food budget')
  await page.waitForTimeout(3400) // let the toast go before the screenshot
  await shot('22-workout-summary')
  const finished = (await apiFetch(`/v1/training/workouts/${workoutId}`)).body
  check(finished?.status === 'completed' && finished?.duration_s === 2100 && finished?.kcal_burned > 0, 'finished: 35 min, kcal estimated', `${finished?.kcal_burned} kcal`)
  await page.getByRole('button', { name: 'Готово' }).click()
  await page.waitForURL(/\/train$/, { timeout: 8000 })
  await page.waitForTimeout(800)
  check(await hasText(`UI Грудь ${tag}`) && (await hasText('Сегодня тренировок')), 'history and today summary updated')

  // a record: the same bench a bit heavier, through the API
  const second = (await apiFetch('/v1/training/workouts', { method: 'POST', body: JSON.stringify({ id: crypto.randomUUID() }) })).body
  const bench = library.find((exercise) => exercise.name_en === 'Barbell bench press')
  const bestBefore = Math.max(...(finished?.exercises?.[0]?.sets ?? []).map((set) => set.weight_kg ?? 0), finished?.exercises?.[0]?.bests?.weight_kg ?? 0)
  await apiFetch(`/v1/training/workouts/${second.id}/sets/${crypto.randomUUID()}`, {
    method: 'PUT',
    body: JSON.stringify({ exercise_id: bench.id, reps: 3, weight_kg: bestBefore + 2.5 }),
  })
  const recordRun = (await apiFetch(`/v1/training/workouts/${second.id}/finish`, { method: 'POST', body: JSON.stringify({ duration_s: 600 }) })).body
  check((recordRun?.records ?? []).some((record) => record.kind === 'weight'), 'heavier set → weight record', `${bestBefore + 2.5} kg`)
  await page.reload({ waitUntil: 'networkidle' })
  check(await page.locator('[data-testid="records-list"]').isVisible(), 'records list on the Train tab')

  // quick cardio from quick add → start sheet → cardio
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await openQuickAdd('Начать тренировку')
  await page.waitForURL(/\/train/, { timeout: 8000 })
  await page.locator('[data-testid="start-sheet"]').waitFor({ timeout: 8000 })
  await page.locator('[data-testid="start-sheet"]').getByRole('button', { name: 'Кардио' }).click()
  await page.locator('#cardio-minutes').fill('20')
  await page.locator('#cardio-distance').fill('3,2')
  await page.getByRole('button', { name: 'Записать кардио' }).click()
  await page.waitForTimeout(1200)
  check(await hasText('Кардио записано'), 'quick cardio logged from quick add (2 taps to the form)')
  await page.goto(`${WEB_URL}/train`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  const bars = await page.locator('[data-testid="burned-chart"] .chart-burn').evaluateAll((els) =>
    els.filter((el) => el.getBoundingClientRect().height > 0).length,
  )
  check(bars >= 1, 'burned chart draws today’s bar', `${bars} bar(s)`)
  await page.locator('[data-testid="burned-chart"]').scrollIntoViewIfNeeded()
  await shot('25-train-burned-chart')

  // Today: the training card and the burned ring (never food budget)
  const dayBefore = (await apiFetch('/v1/nutrition/day')).body
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  const burnedToday = Number((await page.locator('[data-testid="burned-today"]').innerText()).replace(/\D/g, ''))
  const trainingToday = (await apiFetch('/v1/training/today')).body
  check(burnedToday === Math.round(trainingToday?.burned_kcal ?? -1) && burnedToday > 0, 'Today: burned ring shows workout kcal', `${burnedToday} kcal`)
  const dayAfter = (await apiFetch('/v1/nutrition/day')).body
  check(JSON.stringify(dayBefore?.totals) === JSON.stringify(dayAfter?.totals), 'food totals unchanged by workouts')
  check(await page.locator('[data-testid="today-training"]').isVisible(), 'Today: training card is live (no stage placeholder)')
  await page.locator('[data-testid="today-training"]').scrollIntoViewIfNeeded()
  await shot('24-today-training')

  // 7¹⁰. stage 5: health — Shortcut token + ingest, manual steps, weight, sleep, offline, /health
  console.log('\n7¹⁰. stage 5 health')
  const digits = (value) => Number(String(value).replace(/\D/g, ''))
  const foodBefore = (await apiFetch('/v1/nutrition/day')).body?.totals
  const profileWeightBefore = (await apiFetch('/v1/settings')).body?.settings?.weight_kg
  await page.goto(`${WEB_URL}/settings`, { waitUntil: 'networkidle' })
  await page.getByText('Команда iOS', { exact: true }).click()
  await page.waitForURL(/\/settings\/shortcut$/, { timeout: 8000 })
  check(await hasText('Как собрать Команду'), 'Settings → iOS Shortcut: setup steps shown')
  await page.getByRole('button', { name: 'Создать токен' }).click()
  await page.locator('[data-testid="shortcut-token"]').waitFor({ timeout: 8000 })
  const shortcutToken = (await page.locator('[data-testid="shortcut-token"]').innerText()).trim()
  const ingestUrl = (await page.locator('[data-testid="shortcut-url"]').innerText()).trim()
  check(shortcutToken.startsWith('dsos_pat_') && ingestUrl.endsWith('/api/v1/ingest/health'), 'token issued once, URL shown', ingestUrl)
  const asShortcut = (path, body) =>
    apiFetch(path, { method: 'POST', body: JSON.stringify(body), headers: { Authorization: `Bearer ${shortcutToken}` } })
  const pushed = await asShortcut('/v1/ingest/health', { steps: '8 342', active_kcal: '512,5', distance_km: '6,2' })
  check(pushed.status === 200 && pushed.body?.day?.steps === 8342 && pushed.body?.day?.distance_m === 6200, 'Shortcut ingest accepts «8 342», «512,5», km', `${pushed.status}`)
  const replay = await asShortcut('/v1/ingest/health', { steps: '8 342', active_kcal: '512,5', distance_km: '6,2' })
  check(replay.body?.created === false && replay.body?.day?.steps === 8342, 'second run updates the same day (idempotent)')
  offlineMode = true // the expected 403 below is logged by the browser; not an app error
  check((await apiFetch('/v1/settings', { headers: { Authorization: `Bearer ${shortcutToken}` } })).status === 403, 'the Shortcut token cannot read settings')
  offlineMode = false
  await page.reload({ waitUntil: 'networkidle' })
  check(await hasText('прислала данные'), 'Shortcut screen shows the last sync')
  await shot('26-settings-shortcut')

  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  const stepsLine = await page.locator('[data-testid="steps-today"]').innerText()
  check(digits(stepsLine.split('/')[0]) === 8342, 'Today: steps from the Shortcut vs target', stepsLine)
  const healthToday = (await apiFetch('/v1/health/today')).body
  check(
    digits(await page.locator('[data-testid="burned-steps"]').innerText()) === Math.round(healthToday?.day?.steps_kcal ?? -1),
    'Today: step kcal shown apart from workouts (Health active energy)',
    `${healthToday?.day?.steps_kcal} kcal`,
  )

  // quick add → steps (no stage badge) → manual entry replaces the synced number
  await page.locator('nav.tabbar button[aria-label]').first().click()
  await page.waitForTimeout(500)
  const stepsTile = page.locator('[role="dialog"] button').filter({ hasText: 'Шаги' })
  check(!(await stepsTile.innerText()).includes('5'), 'quick add: steps tile has no stage badge')
  await stepsTile.click()
  await page.locator('#steps-input').waitFor({ timeout: 8000 })
  check(await page.locator('[data-testid="steps-synced-note"]').isVisible(), 'steps sheet says the number came from the Shortcut')
  await page.locator('#steps-input').fill('10 000')
  await page.getByRole('button', { name: 'Сохранить шаги' }).click()
  await page.waitForTimeout(1000)
  const manual = (await apiFetch('/v1/health/today')).body?.day
  check(manual?.steps === 10000 && manual?.source === 'manual' && manual?.steps_kcal_estimated === true, 'manual steps saved, kcal estimated', `${manual?.steps_kcal} kcal`)
  check((await page.locator('[data-testid="burned-steps"]').innerText()).startsWith('≈'), 'estimated step kcal marked with ≈')

  // weight → profile
  await page.locator('[data-testid="health-weight-tile"]').click()
  await page.locator('#weight-input').fill('80,4')
  await page.getByRole('button', { name: 'Сохранить вес' }).click()
  await page.waitForTimeout(1200)
  const profileWeight = (await apiFetch('/v1/settings')).body?.settings?.weight_kg
  check(profileWeight === 80.4, 'weigh-in «80,4» saved and synced to the profile', `${profileWeight} kg`)
  check((await page.locator('[data-testid="health-weight-tile"]').innerText()).includes('80,4'), 'Today: weight tile updated')

  // sleep: 23:30 → 07:15
  await page.locator('[data-testid="health-sleep-tile"]').click()
  await page.locator('#bed-input').fill('23:30')
  await page.locator('#wake-input').fill('07:15')
  await page.waitForTimeout(200)
  check((await page.locator('[data-testid="sleep-preview"]').innerText()).includes('7 ч 45 мин'), 'sleep preview: 7 ч 45 мин')
  await shot('29-sleep-sheet')
  await page.getByRole('button', { name: 'Сохранить сон' }).click()
  await page.waitForTimeout(1200)
  const night = (await apiFetch('/v1/health/today')).body?.sleep
  check(night?.duration_min === 465, 'sleep saved: 465 min on the wake-up day', night?.date)

  // offline: yesterday's steps are queued, then sent
  offlineMode = true
  await context.setOffline(true)
  await page.getByRole('button', { name: 'Ввести шаги' }).click()
  await page.getByRole('tab', { name: 'Вчера' }).click()
  await page.locator('#steps-input').fill('4321')
  await page.getByRole('button', { name: 'Сохранить шаги' }).click()
  await page.getByText('Нет сети — сохранится').first().waitFor({ timeout: 8000 }).catch(() => {})
  check(await hasText('Нет сети — сохранится'), 'offline steps are queued with a clear message')
  await context.setOffline(false)
  offlineMode = false
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await page.waitForTimeout(2500)
  const series = (await apiFetch('/v1/health/steps?days=2')).body?.days ?? []
  check(series[0]?.steps === 4321, 'the queued steps reached the server after reconnect', `${series[0]?.date}: ${series[0]?.steps}`)
  await page.waitForTimeout(2500) // toasts gone
  await page.locator('[data-testid="health-today"]').scrollIntoViewIfNeeded()
  await shot('28-today-health')

  // /health page
  await page.getByRole('link', { name: 'Все данные' }).click()
  await page.waitForURL(/\/health$/, { timeout: 8000 })
  await page.locator('[data-testid="steps-chart"]').waitFor({ timeout: 8000 })
  await page.waitForTimeout(600)
  const stepBars = await page.locator('[data-testid="steps-chart"] .chart-steps').evaluateAll((els) =>
    els.filter((el) => el.getBoundingClientRect().height > 0).length,
  )
  check(stepBars >= 2, '/health: steps chart draws today and yesterday', `${stepBars} bars`)
  check((await page.locator('[data-testid="weight-latest"]').innerText()).includes('80,4'), '/health: latest weight 80,4')
  check((await page.locator('[data-testid="sleep-chart"] .chart-sleep').count()) >= 1, '/health: sleep night drawn on the timeline')
  await shot('27-health-page')
  await page.locator('[data-testid="sleep-chart"]').scrollIntoViewIfNeeded()
  await shot('30-health-sleep-weight')
  const foodAfter = (await apiFetch('/v1/nutrition/day')).body?.totals
  check(JSON.stringify(foodBefore) === JSON.stringify(foodAfter), 'food totals unchanged by steps, weight and sleep')

  // clean up: this run's steps, sleep and Shortcut token; the profile weight goes back
  const serverToday = (await apiFetch('/v1/health/today')).body?.day?.date
  const yesterday = new Date(Date.parse(`${serverToday}T12:00:00Z`) - 86_400_000).toISOString().slice(0, 10)
  const cleaned = [
    (await apiFetch(`/v1/health/steps/${serverToday}`, { method: 'DELETE' })).status,
    (await apiFetch(`/v1/health/steps/${yesterday}`, { method: 'DELETE' })).status,
    (await apiFetch(`/v1/health/sleep/${serverToday}`, { method: 'DELETE' })).status,
  ]
  if (profileWeightBefore != null) {
    await apiFetch(`/v1/health/weight/${serverToday}`, { method: 'PUT', body: JSON.stringify({ weight_kg: profileWeightBefore }) })
  }
  for (const token of (await apiFetch('/v1/tokens')).body?.tokens ?? []) {
    if (token.is_active && token.name === 'Команда iOS') await apiFetch(`/v1/tokens/${token.id}`, { method: 'DELETE' })
  }
  const restored = (await apiFetch('/v1/settings')).body?.settings?.weight_kg
  check(cleaned.every((status) => status === 200) && restored === profileWeightBefore, 'stage-5 test data removed, profile weight restored', `${restored} kg`)

  // 8. Reload keeps the session (token persisted).
  // Stage 6+: feature checks live in scripts/ui-checks/<module>.mjs, one module
  // per workstream, each exporting `run(ctx)` with the helpers below.
  // stage9-a11y stays last: it audits every screen with the data the earlier modules leave
  // (stage10-themes and stage10-today leave the appearance as they found it: «Эволюция», dark)
  for (const module of [
    'stage6-plan',
    'stage6-calendar',
    'stage7-notify',
    'stage8-game',
    'stage8-stats',
    'stage9-offline',
    'stage9-notion',
    'stage10-themes',
    'stage10-appearance-sync',
    'stage10-today',
    'stage9-a11y',
  ]) {
    const { run } = await import(`./ui-checks/${module}.mjs`)
    await run({
      page,
      context,
      WEB_URL,
      SHOTS,
      check,
      ok,
      bad,
      shot,
      text,
      hasText,
      setOffline: async (value) => {
        offlineMode = value
        await context.setOffline(value)
      },
      /** Run `action` while the browser logs a 4xx/5xx the check provokes on purpose (not an app error). */
      allowErrors: async (action) => {
        offlineMode = true
        try {
          return await action()
        } finally {
          offlineMode = false
        }
      },
    })
  }

  console.log('\n8. session persistence')
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
  await page.reload({ waitUntil: 'networkidle' })
  check(await hasText('Сегодня'), 'reload keeps the authenticated session')

  // 9. PWA: the manifest + service worker are emitted by `npm run build`
  //    (vite-plugin-pwa keeps devOptions off, so the dev server serves neither).
  console.log('\n9. pwa')
  const headBits = await page.evaluate(() => ({
    manifest: document.querySelector('link[rel="manifest"]')?.getAttribute('href') ?? null,
    themeColor: document.querySelector('meta[name="theme-color"]')?.getAttribute('content') ?? null,
    appleCapable:
      document.querySelector('meta[name="mobile-web-app-capable"]')?.getAttribute('content') ??
      document.querySelector('meta[name="apple-mobile-web-app-capable"]')?.getAttribute('content') ??
      null,
    appleIcon: !!document.querySelector('link[rel="apple-touch-icon"]'),
  }))
  check(!!headBits.themeColor, 'theme-color meta is set', headBits.themeColor ?? '')
  check(headBits.appleCapable === 'yes', 'iOS standalone meta is present', String(headBits.appleCapable))
  check(headBits.appleIcon, 'apple-touch-icon is linked')

  const manifestPath = join(REPO_ROOT, 'web', 'dist', 'manifest.webmanifest')
  if (existsSync(manifestPath)) {
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    check(manifest.display === 'standalone', 'built manifest: display=standalone', manifest.display)
    check(manifest.lang === 'ru', 'built manifest: default language is ru', manifest.lang)
    check(manifest.start_url === '/' && manifest.scope === '/', 'built manifest: start_url + scope are /')
    const icons = manifest.icons ?? []
    check(icons.length >= 3, 'built manifest: 192/512/maskable icons', `${icons.length} icons`)
    check(icons.some((icon) => icon.purpose === 'maskable'), 'built manifest: maskable icon present')
    check((manifest.shortcuts ?? []).length >= 3, 'built manifest: home-screen shortcuts', `${(manifest.shortcuts ?? []).length}`)
    const swPath = join(REPO_ROOT, 'web', 'dist', 'sw.js')
    check(existsSync(swPath), 'built service worker exists (dist/sw.js)')
  } else {
    ok('dist/ not built in this run — manifest is validated by `npm run build` (vite-plugin-pwa)')
  }
} catch (error) {
  bad('script error', String(error?.message ?? error))
} finally {
  console.log('\n10. console & network hygiene')
  const relevantErrors = consoleErrors.filter(
    (line) => !/Download the React DevTools|favicon|404 \(Not Found\)/i.test(line),
  )
  check(relevantErrors.length === 0, 'no console errors', relevantErrors.slice(0, 3).join(' | '))
  check(requestErrors.length === 0, 'no failed / 5xx API requests', requestErrors.slice(0, 3).join(' | '))

  // Best effort: revoke this run's device so the next run has a free slot
  // (MAX_DEVICES_DEFAULT is 2). Set KEEP_DEVICE=1 to keep it paired.
  if (!process.env.KEEP_DEVICE) {
    const revoked = await page
      .evaluate(async () => {
        const token = window.localStorage.getItem('dsos.device_token')
        if (!token) return 'no-token'
        const headers = { Authorization: `Bearer ${token}` }
        const list = await fetch('/api/v1/devices', { headers }).then((r) => r.json())
        const mine = (list.devices ?? []).find((device) => device.is_current)
        if (!mine) return 'not-found'
        const response = await fetch(`/api/v1/devices/${mine.id}`, { method: 'DELETE', headers })
        if (response.status === 409) return `kept: ${mine.name} is the only device (409 last_device)`
        return `${response.status} ${mine.name}`
      })
      .catch((error) => `error ${error?.message ?? error}`)
    console.log(`\ncleanup: this run's device — ${revoked}`)
  }

  await browser.close()
}

console.log(`\nscreenshots: ${SHOTS}`)
if (failures.length) {
  console.error(`\n${failures.length} check(s) failed:`)
  for (const failure of failures) console.error(`  - ${failure}`)
  process.exit(1)
}
console.log('\nAll browser acceptance checks passed.')
