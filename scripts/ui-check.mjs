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
const browser = await chromium.launch({
  args: FAKE_CAMERA
    ? [
        '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream',
        `--use-file-for-fake-video-capture=${FAKE_CAMERA}`,
      ]
    : [],
})
const context = await browser.newContext({
  ...iPhone,
  viewport: { width: 390, height: 844 },
  locale: 'ru-RU',
  timezoneId: 'Europe/Moscow',
  geolocation: undefined,
  permissions: FAKE_CAMERA ? ['geolocation', 'camera'] : ['geolocation'],
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
  if (response.url().includes('/api/') && response.status() >= 500) {
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
    await page.waitForTimeout(800)
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
  check(await hasText('Завтрак') || await hasText('Обед') || await hasText('Ужин'), 'meal appears under its type')
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
  await reviewItems.nth(2).getByRole('button', { name: /^Убрать/ }).click()
  await page.waitForTimeout(300)
  check((await reviewItems.count()) === 2, 'remove an item before saving')
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
  await context.setOffline(false)
  await page.keyboard.press('Escape')
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

  // 8. Reload keeps the session (token persisted).
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
        return `${response.status} ${mine.name}`
      })
      .catch((error) => `error ${error?.message ?? error}`)
    console.log(`\ncleanup: revoked this run's device — ${revoked}`)
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
