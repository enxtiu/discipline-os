/**
 * Browser checks — stage 7 notifications (web push, prefs, quiet hours, history,
 * onboarding on iPhone, `?task=` deep links). Called by scripts/ui-check.mjs.
 *
 * Headless Chromium cannot reach a real push service, so the "on" state uses a
 * subscription registered through the API whose endpoint is a local receiver
 * started here; the test notification that reaches it is decrypted with the
 * generated browser keys (RFC 8291) to prove the whole server path.
 * Everything this module changes is put back at the end.
 */
import { createDecipheriv, createECDH, createHmac, randomBytes, randomUUID } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST = join(resolve(dirname(fileURLToPath(import.meta.url)), '..', '..'), 'web', 'dist')

const b64url = (buffer) => Buffer.from(buffer).toString('base64url')

/** A stand-in for the push service: records every POST. */
function startReceiver() {
  const posts = []
  const server = createServer((request, response) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', () => {
      posts.push({ url: request.url, headers: request.headers, body: Buffer.concat(chunks) })
      response.writeHead(201)
      response.end()
    })
  })
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () =>
      resolve({ server, posts, endpoint: `http://127.0.0.1:${server.address().port}/push/ui-${Date.now()}` }),
    ),
  )
}

/** The browser side of a subscription: an ECDH key pair and the auth secret. */
function browserKeys() {
  const ecdh = createECDH('prime256v1')
  ecdh.generateKeys()
  const auth = randomBytes(16)
  const hmac = (key, data) => createHmac('sha256', key).update(data).digest()
  return {
    json: { p256dh: b64url(ecdh.getPublicKey()), auth: b64url(auth) },
    /** RFC 8291 (aes128gcm, one record) → the JSON payload. */
    decrypt(body) {
      const salt = body.subarray(0, 16)
      const idLength = body[20]
      const senderKey = body.subarray(21, 21 + idLength)
      const ciphertext = body.subarray(21 + idLength)
      const prkKey = hmac(auth, ecdh.computeSecret(senderKey))
      const ikm = hmac(prkKey, Buffer.concat([Buffer.from('WebPush: info\0'), ecdh.getPublicKey(), senderKey, Buffer.from([1])]))
      const prk = hmac(salt, ikm)
      const cek = hmac(prk, Buffer.from('Content-Encoding: aes128gcm\0\x01')).subarray(0, 16)
      const nonce = hmac(prk, Buffer.from('Content-Encoding: nonce\0\x01')).subarray(0, 12)
      const decipher = createDecipheriv('aes-128-gcm', cek, nonce)
      decipher.setAuthTag(ciphertext.subarray(ciphertext.length - 16))
      const plain = Buffer.concat([decipher.update(ciphertext.subarray(0, ciphertext.length - 16)), decipher.final()])
      let end = plain.length - 1
      while (end >= 0 && plain[end] === 0) end -= 1 // padding, then the 0x02 delimiter
      return JSON.parse(plain.subarray(0, end).toString('utf8'))
    },
  }
}

export async function run(ctx) {
  const { page, context, WEB_URL, check, ok, bad, shot, hasText } = ctx
  console.log('\n7¹³. stage 7 notifications')

  const apiFetch = (path, init = {}, target = page) =>
    target.evaluate(
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
  /** The push card reaches this state (its status is computed asynchronously). */
  const pushState = (target, wanted) =>
    target
      .waitForFunction(
        (value) => document.querySelector('[data-testid="push-status"]')?.getAttribute('data-state') === value,
        wanted,
        { timeout: 10_000 },
      )
      .then(() => true)
      .catch(() => false)

  /** Do something and wait for the API call it triggers (both settle together). */
  const after = async (target, method, fragment, action) => {
    const [response] = await Promise.all([
      target.waitForResponse(
        (candidate) => candidate.request().method() === method && candidate.url().includes(fragment),
        { timeout: 10_000 },
      ),
      action(),
    ])
    return response
  }

  // the generated Workbox SW must load the push handlers (vite.config.ts → workbox.importScripts)
  if (existsSync(join(DIST, 'sw.js'))) {
    check(readFileSync(join(DIST, 'sw.js'), 'utf8').includes('importScripts("sw-push.js")'), 'built dist/sw.js imports sw-push.js')
    check(existsSync(join(DIST, 'sw-push.js')), 'dist/sw-push.js is emitted next to it')
  } else {
    ok('dist/ not built in this run — the service-worker import is checked after `npm run build`')
  }

  const config = (await apiFetch('/v1/push/config')).body
  check(config?.enabled === true && !!config?.vapid_public_key, 'push is configured on this API (VAPID keys)')
  const prefsBefore = (await apiFetch('/v1/notifications/prefs')).body
  const settingsBefore = (await apiFetch('/v1/settings')).body?.settings
  const receiver = await startReceiver()
  let desktop = null

  try {
    // 1. iPhone Safari tab (this context: iPhone UA, not standalone) → install first
    await context.addInitScript(() => {
      if (!('standalone' in navigator)) Object.defineProperty(navigator, 'standalone', { get: () => false })
      window.__openedTasks = []
      window.addEventListener('dsos:plan:open-task', (event) => window.__openedTasks.push(event.detail))
    })
    await page.goto(`${WEB_URL}/settings/notifications`, { waitUntil: 'networkidle' })
    check(await pushState(page, 'needs_install'), 'iPhone Safari tab: status says «На экран Домой» first')
    check(await hasText('Нажмите «Поделиться» внизу экрана'), 'the install steps are listed')
    check((await page.getByRole('button', { name: 'Включить уведомления' }).count()) === 0, 'no enable button before the app is installed')
    await shot('40-notifications-iphone-safari')

    // 2. per-category prefs: a toggle and a time, saved on the server, kept after reload
    const water = page.getByRole('switch', { name: 'Вода' })
    const switchBox = await water.boundingBox()
    check(!!switchBox && switchBox.height >= 48, 'a category row is a 48 px switch', switchBox ? `${Math.round(switchBox.height)} px` : 'missing')
    const savedWater = await after(page, 'PUT', '/v1/notifications/prefs', () => water.click())
    check(savedWater.status() === 200, 'water off → PUT /notifications/prefs')
    const mealTime = page.getByLabel('Записать еду: время 1', { exact: true })
    const timeBox = await mealTime.boundingBox()
    check(!!timeBox && timeBox.height >= 48, 'reminder time inputs are 48 px tall', timeBox ? `${Math.round(timeBox.height)} px` : 'missing')
    const savedMeal = await after(page, 'PUT', '/v1/notifications/prefs', async () => {
      await mealTime.fill('12:45')
      await mealTime.blur()
    })
    check(savedMeal.status() === 200, 'meal time 12:45 → saved')
    await page.reload({ waitUntil: 'networkidle' })
    check((await page.getByRole('switch', { name: 'Вода' }).getAttribute('aria-checked')) === 'false', 'water stays off after a reload')
    check((await page.getByLabel('Записать еду: время 1', { exact: true }).inputValue()) === '12:45', 'meal time stays 12:45 after a reload')
    check((await page.locator('[data-testid="notify-water"] input[type="time"]').count()) === 0, 'times are hidden while a category is off')
    const prefs = (await apiFetch('/v1/notifications/prefs')).body
    check(prefs?.water === false && prefs?.meal_times?.[0] === '12:45', 'the server has the prefs', JSON.stringify(prefs?.meal_times))
    await after(page, 'PUT', '/v1/notifications/prefs', () =>
      page.locator('[data-testid="notify-meal_logging"]').getByRole('button', { name: 'Добавить время' }).click(),
    )
    check((await apiFetch('/v1/notifications/prefs')).body?.meal_times?.length === (prefs?.meal_times?.length ?? 0) + 1, 'a reminder time can be added')

    // 3. quiet hours (settings row) are edited on the same screen
    const quietFrom = page.locator('[data-testid="quiet-hours"] input[type="time"]').first()
    const savedQuiet = await after(page, 'PATCH', '/v1/settings', async () => {
      await quietFrom.fill('22:30')
      await quietFrom.blur()
    })
    check(savedQuiet.status() === 200, 'quiet hours start 22:30 → PATCH /settings')
    await page.reload({ waitUntil: 'networkidle' })
    check(
      (await page.locator('[data-testid="quiet-hours"] input[type="time"]').first().inputValue()) === '22:30',
      'quiet hours stay 22:30 after a reload',
    )
    check((await apiFetch('/v1/notifications/prefs')).body?.quiet_hours?.start === '22:30', 'prefs mirror the quiet hours')
    await shot('41-notifications-prefs')

    // 4. a desktop browser that may show notifications, same device: off → on (API) → test → history → off
    const token = await page.evaluate(() => window.localStorage.getItem('dsos.device_token'))
    desktop = await context.browser().newContext({
      viewport: { width: 390, height: 844 },
      locale: 'ru-RU',
      timezoneId: 'Europe/Moscow',
      permissions: ['notifications'],
    })
    await desktop.addInitScript((value) => {
      if (!window.localStorage.getItem('dsos.device_token')) window.localStorage.setItem('dsos.device_token', value)
    }, token)
    const dpage = await desktop.newPage()
    const desktopErrors = []
    dpage.on('console', (message) => {
      if (message.type() === 'error' && !/favicon|404 \(Not Found\)/i.test(message.text())) desktopErrors.push(message.text())
    })
    dpage.on('pageerror', (error) => desktopErrors.push(String(error)))
    const stateIs = (value) => pushState(dpage, value)
    // headless Chromium reports Notification.permission "denied": the denied state, as a user would see it
    await dpage.goto(`${WEB_URL}/settings/notifications`, { waitUntil: 'networkidle' })
    check(await stateIs('denied'), 'notifications blocked → status «Запрещены»')
    check((await dpage.locator('[data-testid="push-status"]').innerText()).includes('значок слева от адреса сайта'), 'denied: how to allow them again')
    // from here on, a browser that allowed notifications
    await desktop.addInitScript(() => {
      Object.defineProperty(Notification, 'permission', { get: () => 'granted' })
      Notification.requestPermission = async () => 'granted'
    })
    await dpage.reload({ waitUntil: 'networkidle' })
    check(await stateIs('off'), 'permission granted, nothing subscribed: status off')
    const enable = dpage.getByRole('button', { name: 'Включить уведомления' })
    check((await enable.count()) === 1, '«Включить уведомления» is offered')
    // no service worker under `vite dev`: enabling explains instead of hanging
    await enable.click()
    const toast = await dpage
      .getByText('Приложение ещё не готово к уведомлениям', { exact: false })
      .waitFor({ timeout: 12_000 })
      .then(() => true)
      .catch(() => false)
    check(toast, 'enable without a service worker → an honest message, no hang')

    const keys = browserKeys()
    const registered = await apiFetch('/v1/push/subscriptions', {
      method: 'POST',
      body: JSON.stringify({ endpoint: receiver.endpoint, keys: keys.json, user_agent: 'ui-check' }),
    }, dpage)
    check(registered.status === 201 && registered.body?.is_current_device === true, 'subscription registered for this device (local receiver)', `HTTP ${registered.status}`)
    await dpage.reload({ waitUntil: 'networkidle' })
    check(await stateIs('on'), 'status on → test and disable buttons')
    // badges are uppercased by CSS and innerText follows text-transform: compare in lower case
    const devicesText = (await dpage.locator('[data-testid="push-devices"]').innerText()).toLowerCase()
    check(devicesText.includes('это устройство') && devicesText.includes('127.0.0.1'), 'device list shows this device and its push host')

    const testResponse = await after(dpage, 'POST', '/v1/push/test', () =>
      dpage.getByRole('button', { name: 'Отправить тестовое' }).click(),
    )
    const testBody = await testResponse.json()
    check(testResponse.status() === 200 && testBody.sent >= 1, '«Отправить тестовое» → POST /push/test', JSON.stringify(testBody))
    await dpage.waitForTimeout(500)
    check(receiver.posts.length === 1, 'the receiver got exactly one push', `${receiver.posts.length} POST(s)`)
    const push = receiver.posts[0]
    if (push) {
      check(push.headers['content-encoding'] === 'aes128gcm', 'the push is aes128gcm-encrypted')
      check(
        (push.headers.authorization ?? '').startsWith('vapid t=') && push.headers.authorization.endsWith(`k=${config?.vapid_public_key}`),
        'the push carries our VAPID signature',
      )
      let payload = null
      try {
        payload = keys.decrypt(push.body)
      } catch (error) {
        bad('the payload decrypts with the browser keys', String(error))
      }
      const expected = settingsBefore?.language === 'en' ? 'Test notification' : 'Проверка уведомлений'
      check(payload?.title === expected && payload?.url === '/settings/notifications', 'decrypted payload: the test title and its link', payload?.title ?? '')
    }
    await dpage.waitForTimeout(400)
    const history = dpage.locator('[data-testid="push-history"]')
    await history.waitFor({ timeout: 8000 }).catch(() => {})
    const historyText = (await history.innerText().catch(() => '')).replace(/\u00a0/g, ' ').toLowerCase()
    check(historyText.includes('проверка уведомлений') && historyText.includes('доставлено'), 'history lists the delivered test notification')
    await dpage.screenshot({ path: `${ctx.SHOTS}/42-notifications-on.png`, fullPage: true })

    const disabled = await after(dpage, 'DELETE', '/v1/push/subscriptions/', () =>
      dpage.getByRole('button', { name: 'Выключить на этом устройстве' }).click(),
    )
    check(disabled.status() === 204, '«Выключить на этом устройстве» removes the subscription')
    check(await stateIs('off'), 'status back to off')
    const left = (await apiFetch('/v1/push/subscriptions', {}, dpage)).body?.items ?? []
    check(!left.some((item) => item.id === registered.body?.id), 'the server forgot the subscription')
    check(desktopErrors.length === 0, 'no console errors in the desktop browser', desktopErrors.slice(0, 2).join(' | '))
    await desktop.close()
    desktop = null

    // 5. onboarding on an iPhone Safari tab: «На экран Домой» first, skippable
    await page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    check((await page.evaluate(() => navigator.standalone)) === false, 'Safari mode: navigator.standalone is false')
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('dsos:open-onboarding')))
    await page.getByText('Настроим расчёты').waitFor({ timeout: 8000 })
    for (let step = 0; step < 3; step += 1) {
      await page.getByRole('button', { name: 'Далее' }).click()
      await page.waitForTimeout(450)
    }
    const card = page.locator('[data-testid="push-status"]')
    check(await pushState(page, 'needs_install'), 'onboarding (last step) knows push needs the Home Screen app')
    check(await hasText('Сначала установите его'), 'onboarding explains «На экран Домой» first, with the steps')
    check(await hasText('Можно и позже'), 'onboarding says it can be done later (skippable)')
    await card.scrollIntoViewIfNeeded()
    await shot('43-onboarding-push-iphone')
    await page.getByRole('button', { name: 'Пропустить' }).click()
    await page.waitForTimeout(600)
    check(!(await hasText('Настроим расчёты')), 'skipping closes the wizard')

    // 6. ?task=<id> anywhere opens that task (planner event) and the parameter goes away
    const created = await apiFetch('/v1/planner/tasks', { method: 'POST', body: JSON.stringify({ title: 'Задача из уведомления' }) })
    const taskId = created.status === 201 ? created.body.id : randomUUID()
    if (created.status !== 201) ok('planner API not in this branch — a random task id stands in', `HTTP ${created.status}`)
    for (const route of ['/today', '/plan']) {
      await page.goto(`${WEB_URL}${route}?task=${taskId}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(500)
      const opened = await page.evaluate(() => window.__openedTasks ?? [])
      check(opened.includes(taskId), `${route}?task=<id> opens the task sheet event`, opened.join(','))
      check(!page.url().includes('task='), `${route}: the task parameter is dropped from the URL`)
      check((await page.locator('[role="dialog"]').count()) >= 1, `${route}: the task sheet is shown`)
      await page.keyboard.press('Escape')
      await page.locator('[role="dialog"]').first().waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
    }
    if (created.status === 201) await apiFetch(`/v1/planner/tasks/${taskId}`, { method: 'DELETE' })
  } catch (error) {
    bad('stage 7 checks', String(error?.message ?? error))
  } finally {
    if (desktop) await desktop.close().catch(() => {})
    receiver.server.close()
    // put back what this module changed
    if (prefsBefore) {
      const { quiet_hours: _q, timezone: _tz, push_enabled: _p, active_subscriptions: _a, ...restore } = prefsBefore
      await apiFetch('/v1/notifications/prefs', { method: 'PUT', body: JSON.stringify(restore) }).catch(() => {})
    }
    if (settingsBefore) {
      await apiFetch('/v1/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          quiet_hours_enabled: settingsBefore.quiet_hours_enabled,
          quiet_hours_start: settingsBefore.quiet_hours_start,
          quiet_hours_end: settingsBefore.quiet_hours_end,
        }),
      }).catch(() => {})
    }
    const after = (await apiFetch('/v1/notifications/prefs').catch(() => ({ body: null }))).body
    check(
      JSON.stringify(after?.meal_times) === JSON.stringify(prefsBefore?.meal_times) &&
        after?.water === prefsBefore?.water &&
        after?.quiet_hours?.start === prefsBefore?.quiet_hours?.start,
      'stage-7 test data removed, prefs and quiet hours restored',
    )
  }
}
