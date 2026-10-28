/**
 * Browser checks — stage 10: «Одинаково на всех устройствах» (Настройки → Оформление). Called by
 * scripts/ui-check.mjs after stage10-themes. Several browser contexts are devices of
 * one account (the same device token, separate storage — as the stage-9 conflict checks do):
 *
 *  - the switch: in the «Устройства» group of Оформление, ON by default, a 48 px target, stored on
 *    the device (`dsos.appearance.sync`), calm copy that follows the state;
 *  - ON syncs: a fresh device uploads nothing; a change is applied at once (local first), saved
 *    to the account after a pause (three taps in a row = one PATCH, with `base`); a second device
 *    that starts, or comes back online, shows it; a change there reaches the first without a reload;
 *  - OFF does not: a device with the switch off neither sends its changes nor takes the account's
 *    (at start or when it comes back online) — and sends no PATCH; turning it on again takes the
 *    account's look; with no account appearance, turning it on uploads this device's;
 *  - offline: the change is kept («Нет связи — сохраню…») and goes out when the network is back;
 *  - a conflict (the account changed the same key meanwhile): this device's own choice wins on that
 *    key, every other key follows the account — no dialog;
 *  - a `base` left in the browser by another account is ignored (nothing leaks between accounts);
 *  - the account's appearance is left as it was found (null).
 */
const SYNC_KEY = 'dsos.appearance.sync'

export async function run(ctx) {
  const { context, WEB_URL, SHOTS, check, bad } = ctx
  console.log('\n10b. stage 10 — «Одинаково на всех устройствах» (two browsers = two devices)')
  await ctx.page.goto('about:blank').catch(() => {})
  const browser = context.browser()

  // the main tab's token (the same account); its page is not used
  const token = await (async () => {
    const probe = await context.newPage()
    await probe.goto(`${WEB_URL}/today`, { waitUntil: 'domcontentloaded' })
    const value = await probe.evaluate(() => window.localStorage.getItem('dsos.device_token'))
    await probe.close()
    return value
  })()
  if (!token) {
    bad('appearance sync: the device token of the run', 'missing')
    return
  }

  const errors = []
  const devices = []
  const open = async (name, { storage = {} } = {}) => {
    const deviceContext = await browser.newContext({
      viewport: { width: 390, height: 844 },
      locale: 'ru-RU',
      timezoneId: 'Europe/Moscow',
    })
    await deviceContext.addInitScript(
      ({ token, storage }) => {
        try {
          // the first visit only: a reload must keep what the app stored
          if (window.localStorage.getItem('dsos.device_token')) return
          window.localStorage.setItem('dsos.device_token', token)
          for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value)
        } catch {
          /* an opaque document (about:blank) has no storage */
        }
      },
      { token, storage },
    )
    const page = await deviceContext.newPage()
    const patches = []
    page.on('request', (request) => {
      if (request.method() === 'PATCH' && /\/api\/v1\/settings$/.test(request.url()) && !request.headers()['x-check']) {
        patches.push(JSON.parse(request.postData() ?? '{}'))
      }
    })
    page.on('console', (message) => {
      if (message.type() === 'error' && !/favicon|404 \(Not Found\)|Failed to load resource/i.test(message.text())) {
        errors.push(`${name}: ${message.text()}`)
      }
    })
    page.on('pageerror', (error) => errors.push(`${name}: ${String(error)}`))
    const device = { name, context: deviceContext, page, patches }
    devices.push(device)
    return device
  }

  const api = (page, path, init = {}) =>
    page.evaluate(
      async ({ path, init }) => {
        const response = await fetch(`/api${path}`, {
          ...init,
          headers: {
            'Content-Type': 'application/json',
            'X-Check': '1', // the check's own call: not a write of the app
            Authorization: `Bearer ${window.localStorage.getItem('dsos.device_token')}`,
          },
        })
        const raw = await response.text()
        return { status: response.status, body: raw ? JSON.parse(raw) : null }
      },
      { path, init },
    )
  const account = async (page) => (await api(page, '/v1/settings')).body?.settings?.appearance ?? null
  const look = (page) =>
    page.evaluate(() => {
      const d = document.documentElement.dataset
      return { skin: d.skin, theme: d.theme, ground: d.ground, tilt: d.tilt, shadow: d.shadow, text: d.text, motion: d.motion }
    })
  const stored = (page) =>
    page.evaluate((key) => ({ sync: window.localStorage.getItem(key), look: window.localStorage.getItem('dsos.appearance') }), SYNC_KEY)
  const until = async (read, done, timeout = 9000) => {
    const end = Date.now() + timeout
    let value = await read()
    while (!done(value) && Date.now() < end) {
      await new Promise((resolve) => setTimeout(resolve, 150))
      value = await read()
    }
    return value
  }
  const goTheme = async (page) => {
    await page.goto(`${WEB_URL}/settings/theme`, { waitUntil: 'networkidle' })
    await page.locator('[data-testid="theme-section"]').waitFor({ timeout: 10_000 })
  }
  const toggle = (page) => page.getByRole('switch', { name: 'Одинаково на всех устройствах' })
  const saved = (page) => page.waitForResponse((r) => r.request().method() === 'PATCH' && /\/api\/v1\/settings$/.test(r.url()), { timeout: 10_000 })
  /** The sync group at the end of the screen, in view. */
  const shotOf = async (page, name) => {
    await page.locator('[data-testid="theme-sync"]').scrollIntoViewIfNeeded()
    await page.waitForTimeout(250)
    await page.screenshot({ path: `${SHOTS}/${name}.png` })
  }
  const wake = (page) => page.evaluate(() => window.dispatchEvent(new Event('online'))) // what a phone does when it is back online

  let a
  let b
  let c
  let admin
  try {
    // the account starts without an appearance (the earlier modules ran with the switch off)
    a = await open('A')
    await a.page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    await api(a.page, '/v1/settings', { method: 'PATCH', body: JSON.stringify({ appearance: null }) })
    check((await account(a.page)) === null, 'the account has no appearance yet (never synced = null)')

    // ---- 1. the switch ---------------------------------------------------------------------
    await goTheme(a.page)
    const sw = toggle(a.page)
    const box = await sw.boundingBox()
    check(
      (await sw.getAttribute('aria-checked')) === 'true' && (await a.page.locator('[data-testid="theme-sync"]').innerText()).includes('сохраняются в аккаунте'),
      '«Одинаково на всех устройствах» is on by default, with calm copy about the account',
    )
    check(!!box && box.height >= 48 && box.width >= 48, 'the switch is a 48 px target', box ? `${Math.round(box.width)} × ${Math.round(box.height)}` : '')
    check(
      (await a.page.locator('#look-sync').innerText()).toLowerCase().includes('устройства') &&
        (await a.page.locator('[data-testid="theme-sync"]').getAttribute('data-testid')) === 'theme-sync',
      'its group is «Устройства» at the end of Оформление',
    )
    await a.page.waitForTimeout(1500)
    check(a.patches.length === 0 && (await account(a.page)) === null, 'a fresh device uploads nothing (its look is the factory one)', `${a.patches.length} writes`)
    await shotOf(a.page, 's10-sync-on-evolution')

    // ---- 2. ON: a change is local first, then saved to the account ---------------------------
    await a.page.locator('[data-skin-option="stickers"]').click()
    const instant = await look(a.page) // no await in between: before any request can have returned
    check(instant.skin === 'stickers', 'the preview is instant (the page repaints before anything is sent)', instant.skin)
    let response = await saved(a.page)
    check(response.status() === 200, 'the change is saved to the account after a short pause', `PATCH ${response.status()}`)
    const first = a.patches[0]
    check(
      first?.appearance?.skin === 'stickers' && first?.base?.appearance === null,
      'the first write sends the device\'s appearance with base null (the account had none)',
      JSON.stringify(first),
    )
    check((await account(a.page))?.skin === 'stickers', 'GET /settings shows it', JSON.stringify(await account(a.page)))
    // three taps in a row = one write, with the key and the base it started from
    const before = a.patches.length
    await a.page.locator('[data-ground-option="mint"]').click()
    await a.page.locator('[data-ground-option="peach"]').click()
    await a.page.locator('[data-ground-option="lemonade"]').click()
    response = await saved(a.page)
    await a.page.waitForTimeout(1200)
    const burst = a.patches.slice(before)
    check(
      burst.length === 1 && JSON.stringify(burst[0].appearance) === '{"ground":"lemonade"}' && JSON.stringify(burst[0].base) === '{"appearance":{"ground":"lilac"}}',
      'three taps within a moment are one write: {ground: lemonade} with base {ground: lilac}',
      JSON.stringify(burst),
    )
    await a.page.getByRole('tab', { name: 'Светлая', exact: true }).click()
    await saved(a.page)
    const stateA = await account(a.page)
    check(
      stateA?.skin === 'stickers' && stateA?.ground === 'lemonade' && stateA?.mode === 'light' && stateA?.tilt === true,
      'the account holds skin, ground and mode (snake_case, complete)',
      JSON.stringify(stateA),
    )
    check((await a.page.locator('[data-testid="theme-sync-status"]').getAttribute('data-status')) === 'idle', 'the switch\'s status is quiet when everything is saved')

    // ---- 3. a second device takes the account's look at start --------------------------------
    b = await open('B')
    await b.page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    const startB = await until(() => look(b.page), (value) => value.skin === 'stickers')
    check(
      startB.skin === 'stickers' && startB.ground === 'lemonade' && startB.theme === 'light',
      'a second device (ON by default) shows the account\'s look after start',
      JSON.stringify(startB),
    )
    check(b.patches.length === 0, 'and sends nothing back (it only adopted)', `${b.patches.length} writes`)
    await b.page.screenshot({ path: `${SHOTS}/s10-sync-device-b-today.png` })

    // B changes a key; A shows it when it is back online — no reload
    const writesA = a.patches.length
    await goTheme(b.page)
    await b.page.getByRole('switch', { name: 'Наклон наклеек' }).click()
    await saved(b.page)
    check((await account(b.page))?.tilt === false, 'B\'s change reaches the account', JSON.stringify(await account(b.page)))
    await wake(a.page)
    const liveA = await until(() => look(a.page), (value) => value.tilt === 'off')
    check(liveA.tilt === 'off' && liveA.ground === 'lemonade', 'A follows when it is back online (no reload): tilt off, the rest unchanged', JSON.stringify(liveA))
    await a.page.waitForTimeout(1500)
    check(a.patches.length === writesA, 'A did not write it back', `${a.patches.length - writesA} writes`)

    // ---- 4. OFF: neither reads nor writes ----------------------------------------------------
    const writesB = b.patches.length
    await toggle(b.page).click()
    await b.page.waitForTimeout(300)
    check(
      (await toggle(b.page).getAttribute('aria-checked')) === 'false' &&
        (await stored(b.page)).sync === 'off' &&
        (await b.page.locator('[data-testid="theme-sync"]').innerText()).includes('хранит своё оформление'),
      'turning it off is stored on the device (dsos.appearance.sync = off) and the copy says this device keeps its own look',
    )
    await b.page.locator('[data-skin-option="evolution"]').click()
    await b.page.getByRole('tab', { name: 'Тёмная', exact: true }).click()
    await b.page.waitForTimeout(2000)
    check(
      b.patches.length === writesB && (await account(b.page))?.skin === 'stickers' && (await account(b.page))?.mode === 'light',
      'OFF does not write: B changed skin and mode, the account still has stickers / light',
      `${b.patches.length - writesB} writes`,
    )
    // the account changes (A), B comes back online and restarts: B keeps its own look
    await a.page.locator('[data-ground-option="peach"]').click()
    await saved(a.page)
    await wake(b.page)
    await b.page.waitForTimeout(1500)
    await b.page.reload({ waitUntil: 'networkidle' })
    await b.page.waitForTimeout(1200)
    const offB = await look(b.page)
    const offStore = await stored(b.page)
    check(
      offB.skin === 'evolution' && offB.theme === 'dark' && offStore.sync === 'off',
      'OFF does not read: after the account changed and B restarted, B keeps its own look',
      JSON.stringify(offB),
    )
    check(b.patches.length === writesB, 'and B sent no write at all while off', `${b.patches.length - writesB} writes`)
    await goTheme(b.page)
    await shotOf(b.page, 's10-sync-off-evolution')

    // ---- 5. ON again: the account's look comes here ---------------------------------------
    await goTheme(b.page)
    await toggle(b.page).click()
    const backB = await until(() => look(b.page), (value) => value.skin === 'stickers')
    check(
      backB.skin === 'stickers' && backB.ground === 'peach' && backB.theme === 'light' && (await stored(b.page)).sync === 'on',
      'turning it on again applies the account\'s look here (stickers · peach · light)',
      JSON.stringify(backB),
    )
    check(b.patches.length === writesB, 'without writing anything', `${b.patches.length - writesB} writes`)

    // ---- 6. first enable, no appearance on the account: this device's is uploaded -------------
    // A and B are closed first (an open device would put its look back); a plain page does the API calls
    for (const device of [a, b]) {
      await device.context.close().catch(() => {})
      devices.splice(devices.indexOf(device), 1)
    }
    admin = await open('admin')
    await admin.page.goto(`${WEB_URL}/today`, { waitUntil: 'domcontentloaded' })
    await api(admin.page, '/v1/settings', { method: 'PATCH', body: JSON.stringify({ appearance: null }) })
    c = await open('C', {
      storage: {
        [SYNC_KEY]: 'off',
        'dsos.theme': 'light',
        'dsos.appearance': JSON.stringify({ skin: 'stickers', ground: 'mint', tilt: false, shadow: 'soft', largeText: true, reduceMotion: false }),
      },
    })
    await goTheme(c.page)
    await c.page.waitForTimeout(1500)
    check(
      c.patches.length === 0 && (await account(c.page)) === null && (await toggle(c.page).getAttribute('aria-checked')) === 'false',
      'a device with the switch off uploads nothing at start, whatever it holds',
    )
    await toggle(c.page).click()
    await saved(c.page)
    const up = await account(c.page)
    check(
      up?.skin === 'stickers' && up?.ground === 'mint' && up?.tilt === false && up?.shadow === 'soft' && up?.large_text === true && up?.mode === 'light' && up?.reduce_motion === false,
      'first enable with nothing on the account uploads this device\'s appearance',
      JSON.stringify(up),
    )

    // ---- 7. offline: kept, and sent when the network is back --------------------------------
    await c.page.getByRole('switch', { name: 'Крупный текст' }).click() // large text off
    await saved(c.page)
    await c.context.setOffline(true)
    await c.page.locator('[data-ground-option="peach"]').click()
    const offlineLook = await look(c.page)
    await c.page.waitForTimeout(1500)
    const waiting = await c.page.locator('[data-testid="theme-sync-status"]').getAttribute('data-status')
    const waitingText = await c.page.locator('[data-testid="theme-sync-status"]').innerText()
    check(
      offlineLook.ground === 'peach' && waiting === 'waiting' && waitingText.includes('Нет связи'),
      'offline: the look changes at once and the switch says «Нет связи — сохраню, когда она появится»',
      `${offlineLook.ground} ${waiting} ${waitingText}`,
    )
    check((await account(admin.page))?.ground === 'mint', 'nothing reached the account meanwhile', (await account(admin.page))?.ground)
    await c.context.setOffline(false)
    await wake(c.page)
    const synced = await until(() => account(admin.page), (value) => value?.ground === 'peach')
    check(synced?.ground === 'peach', 'back online: the waiting change is sent', JSON.stringify(synced))
    check((await c.page.locator('[data-testid="theme-sync-status"]').getAttribute('data-status')) === 'idle', 'and the status is quiet again')

    // ---- 8. a conflict: this device's key wins, the others follow the account ------------------
    // the account changes behind C's back (another device): ground → lemonade, mode → dark
    const other = await api(admin.page, '/v1/settings', { method: 'PATCH', body: JSON.stringify({ appearance: { ground: 'lemonade', mode: 'dark' } }) })
    check(other.status === 200, 'another device changes the account (ground lemonade, mode dark)')
    const writesC = c.patches.length
    await c.page.locator('[data-ground-option="mint"]').click() // C still thinks the ground is peach
    await until(async () => (await account(admin.page))?.ground, (ground) => ground === 'mint')
    await c.page.waitForTimeout(1200)
    const merged = await account(admin.page)
    const lookC = await look(c.page)
    const conflictWrites = c.patches.slice(writesC)
    check(
      merged?.ground === 'mint' && merged?.mode === 'dark' && lookC.ground === 'mint' && lookC.theme === 'dark',
      'a stale base answers 409 and is merged: C\'s own ground wins, the mode follows the account (no dialog)',
      `${JSON.stringify(merged)} · C ${lookC.ground}/${lookC.theme}`,
    )
    check(
      conflictWrites.length === 2 && JSON.stringify(conflictWrites[0].base) === '{"appearance":{"ground":"peach"}}' && JSON.stringify(conflictWrites[1].base) === '{"appearance":{"ground":"lemonade"}}',
      'it took two writes: the first with the old base (refused), the second with the account\'s',
      JSON.stringify(conflictWrites),
    )

    // ---- 9. another account on the same browser: its `base` is not this account's ---------------
    // E holds a look of its own and the `base` of some other account that differs from it in three keys; were that
    // `base` trusted, E would call those keys "changed here" and send them. It must be treated as never seen.
    const now = await account(admin.page)
    const e = await open('E', {
      storage: {
        'dsos.theme': 'light',
        'dsos.appearance': JSON.stringify({ skin: 'evolution', ground: 'peach', tilt: true, shadow: 'hard', largeText: false, reduceMotion: false }),
        'dsos.appearance.base': JSON.stringify({ user_id: '00000000-0000-0000-0000-00000000dead', appearance: now }),
      },
    })
    await e.page.goto(`${WEB_URL}/today`, { waitUntil: 'networkidle' })
    const lookE = await until(() => look(e.page), (value) => value.skin === 'stickers')
    await e.page.waitForTimeout(1500)
    check(
      lookE.skin === 'stickers' && lookE.theme === 'dark' && lookE.ground === 'mint' && e.patches.length === 0 && JSON.stringify(await account(admin.page)) === JSON.stringify(now),
      'a `base` left by another account is ignored: the device takes this account\'s look and sends nothing',
      `${JSON.stringify(lookE)} · ${e.patches.length} writes`,
    )

    // ---- 10. screens -----------------------------------------------------------------------
    await goTheme(c.page)
    await shotOf(c.page, 's10-sync-on-stickers')
    check(errors.length === 0, 'no console errors on the devices', errors.slice(0, 2).join(' | '))
  } catch (error) {
    bad('appearance sync', String(error?.stack ?? error?.message ?? error).split('\n').slice(0, 4).join(' | '))
  } finally {
    // leave the account as found: no appearance
    if (devices[0]) {
      const reset = await api(devices[0].page, '/v1/settings', { method: 'PATCH', body: JSON.stringify({ appearance: null }) }).catch(() => ({ status: 0 }))
      const left = await account(devices[0].page).catch(() => 'error')
      check(reset.status === 200 && left === null, 'the account\'s appearance is reset to null (left as found)')
    }
    for (const device of devices) await device.context.close().catch(() => {})
  }
}
