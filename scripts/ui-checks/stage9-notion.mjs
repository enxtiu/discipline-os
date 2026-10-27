/**
 * Browser checks — stage 9 Notion sync (Settings → Интеграции). Called by scripts/ui-check.mjs.
 *
 * The dev API has no Notion access, so the real API answers everything that does not
 * need Notion: the not-connected state, and the errors it returns *before* any request
 * to Notion (a malformed secret, a link that is not a Notion page). Only the connected
 * card is rendered from a fixture — GET/PATCH of /api/v1/integrations/notion routed
 * in the browser — because reaching that state needs a real Notion connection; the
 * route is removed again afterwards. No secret typed here is ever rendered back or
 * stored in the browser. Everything this module changes is put back.
 */
import { randomBytes } from 'node:crypto'

const PORTAL = 'https://app.notion.com/developers/connections'

/** What GET /integrations/notion answers for a connected owner (the schema of app/schemas/integrations.py). */
function connectedFixture({ status = 'connected', error = null } = {}) {
  const kind = (name, database, total, extra = {}) => ({
    kind: name,
    enabled: true,
    database,
    url: database === 'ok' ? `https://app.notion.com/p/${randomBytes(16).toString('hex')}` : null,
    last_run:
      database === 'ok'
        ? { created: 0, updated: 1, trashed: 0, unchanged: total - 1, failed: 0, pending: 0, total, state: 'ok' }
        : null,
    ...extra,
  })
  const now = new Date()
  return {
    provider: 'notion',
    status,
    connected: true,
    secret_set: true,
    bot_name: 'DisciplineOS',
    workspace_name: 'Мой Notion',
    page: { id: '1f2e3d4c-5b6a-4987-8a1b-2c3d4e5f6a7b', title: 'DisciplineOS', url: 'https://app.notion.com/p/1f2e3d4c5b6a49878a1b2c3d4e5f6a7b' },
    kinds: [
      kind('areas', 'ok', 6),
      kind('projects', 'ok', 4),
      kind('tasks', 'ok', 57),
      kind('journal', 'ok', 12, { enabled: false }),
      kind('days', 'missing', 0),
    ],
    sync: {
      state: 'idle',
      run_id: '01990000-0000-7000-8000-000000000001',
      started_at: new Date(now.getTime() - 5 * 60_000).toISOString(),
      finished_at: new Date(now.getTime() - 4 * 60_000).toISOString(),
      last_sync_at: new Date(now.getTime() - 4 * 60_000).toISOString(),
      result: 'ok',
      error: status === 'error' ? error : 'notion_database_missing',
      error_kind: null,
      totals: { created: 3, updated: 2, trashed: 1, unchanged: 73, failed: 0, pending: 0, total: 79 },
      requests: 9,
    },
    error: status === 'error' ? error : null,
    notion_version: '2026-03-11',
    interval_minutes: 10,
  }
}

export async function run(ctx) {
  const { page, WEB_URL, check, ok, shot, text, hasText, allowErrors } = ctx
  console.log('\n9ⁿ. stage 9 Notion sync (Settings → Интеграции)')

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
        return { status: response.status, body, raw }
      },
      { path, init },
    )
  const connectCalls = []
  const onRequest = (request) => {
    if (request.url().includes('/api/v1/integrations/notion/connect')) connectCalls.push(request.method())
  }
  page.on('request', onRequest)
  const height = async (locator) => (await locator.boundingBox())?.height ?? 0
  const secrets = []
  const nothingStored = async () =>
    page.evaluate((values) => {
      const stores = [window.localStorage, window.sessionStorage]
      const dump = stores.map((store) => Object.keys(store).map((key) => `${key}=${store.getItem(key)}`).join('\n')).join('\n')
      const shown = document.body.innerText
      return values.every((value) => !dump.includes(value) && !shown.includes(value))
    }, secrets)

  try {
    // 1. the Settings hub leads there; the screen is its own lazy chunk
    await page.goto(`${WEB_URL}/settings`, { waitUntil: 'networkidle' })
    check(await hasText('Интеграции'), 'Settings lists «Интеграции»')
    await page.getByText('Интеграции', { exact: true }).click()
    await page.waitForURL('**/settings/integrations', { timeout: 10_000 }).catch(() => {})
    await page.getByTestId('notion-card').waitFor({ timeout: 10_000 }).catch(() => {})
    check(page.url().endsWith('/settings/integrations'), 'the row opens /settings/integrations', page.url())

    // 2. not connected: what it does, the verified steps, the form
    const state = await apiFetch('/v1/integrations/notion')
    check(
      state.status === 200 && state.body?.connected === false && state.body?.secret_set === false,
      'GET /integrations/notion: not connected, no secret',
      `${state.status} connected=${state.body?.connected}`,
    )
    check(
      (await page.getByTestId('notion-card').getAttribute('data-state')) === 'disconnected',
      'the Notion card shows the not-connected state',
    )
    const body = await text()
    check(body.includes('Односторонняя копия') && body.includes('перезапишутся'), 'one-way: edits in Notion are overwritten (said up front)')
    check(
      ['Internal connections', 'Read content', 'Insert content', 'Connections', 'Add connection', 'Copy link'].every((part) =>
        body.includes(part),
      ),
      'the steps: create a connection, its capabilities, share the page, copy the link',
    )
    const portal = page.getByTestId('notion-portal-link')
    check(
      (await portal.getAttribute('href')) === PORTAL &&
        (await portal.getAttribute('target')) === '_blank' &&
        (await portal.getAttribute('rel'))?.includes('noopener'),
      'a link to Notion’s Developer portal (new tab, noopener)',
      await portal.getAttribute('href'),
    )
    const secretInput = page.locator('input[name="notion-secret"]')
    const pageInput = page.locator('input[name="notion-page"]')
    check((await secretInput.getAttribute('type')) === 'password', 'the secret is a password field (never shown)')
    const connectButton = page.getByTestId('notion-connect')
    const sizes = [await height(connectButton), await height(portal), await height(secretInput), await height(pageInput)]
    check(sizes.every((value) => value >= 47.5), '48 px targets: connect, portal link, both fields', sizes.map(Math.round).join(' / '))
    await shot('60-notion-connect')

    // 3. the form checks itself before sending anything
    const before = connectCalls.length
    await connectButton.click()
    await page.waitForTimeout(300)
    check(
      (await hasText('Вставьте секрет подключения')) && (await hasText('Вставьте ссылку на страницу')),
      'empty fields → both errors inline',
    )
    check(connectCalls.length === before, 'nothing is sent while the form is incomplete')

    // 4. the server's errors in words (it refuses both before calling Notion)
    const badSecret = `ntn bad ${randomBytes(6).toString('hex')}`
    secrets.push(badSecret)
    await secretInput.fill(badSecret)
    await pageInput.fill('https://www.notion.so/myspace/DisciplineOS-1f2e3d4c5b6a49878a1b2c3d4e5f6a7b')
    const refused = await allowErrors(async () => {
      const [response] = await Promise.all([
        page.waitForResponse((r) => r.url().includes('/integrations/notion/connect'), { timeout: 10_000 }),
        connectButton.click(),
      ])
      await page.waitForTimeout(400)
      return { status: response.status(), body: await response.json().catch(() => null) }
    })
    check(
      refused.status === 422 && refused.body?.error?.code === 'notion_secret_invalid',
      'a malformed secret → 422 notion_secret_invalid',
      `${refused.status} ${refused.body?.error?.code}`,
    )
    check(await hasText('Это не похоже на секрет подключения Notion'), 'the refusal is explained under the secret field')
    await shot('62-notion-error')

    const fakeSecret = `ntn_${randomBytes(20).toString('hex')}`
    secrets.push(fakeSecret)
    await secretInput.fill(fakeSecret)
    await pageInput.fill('https://example.com/not-notion')
    const wrongPage = await allowErrors(async () => {
      const [response] = await Promise.all([
        page.waitForResponse((r) => r.url().includes('/integrations/notion/connect'), { timeout: 10_000 }),
        connectButton.click(),
      ])
      await page.waitForTimeout(400)
      return { status: response.status(), body: await response.json().catch(() => null) }
    })
    check(
      wrongPage.status === 422 && wrongPage.body?.error?.code === 'notion_page_invalid',
      'a link that is not a Notion page → 422 notion_page_invalid (no request to Notion)',
      `${wrongPage.status} ${wrongPage.body?.error?.code}`,
    )
    check(await hasText('Это не ссылка на страницу Notion'), 'the refusal is explained under the page field')
    check(
      !JSON.stringify(refused.body).includes(badSecret) && !JSON.stringify(wrongPage.body).includes(fakeSecret),
      'error answers never echo the secret',
    )
    check(await nothingStored(), 'no secret is rendered on the page or kept in local/session storage')
    const after = await apiFetch('/v1/integrations/notion')
    check(after.body?.connected === false && !secrets.some((value) => after.raw.includes(value)), 'still not connected, nothing stored')

    // 5. light theme
    await page.evaluate(() => window.localStorage.setItem('dsos.theme', 'light'))
    await page.reload({ waitUntil: 'networkidle' })
    await page.getByTestId('notion-card').waitFor({ timeout: 10_000 }).catch(() => {})
    check(
      (await page.evaluate(() => document.documentElement.dataset.theme)) === 'light',
      'the screen renders in the light theme',
    )
    check((await secretInput.inputValue()) === '', 'a reload never refills the secret')
    await shot('61-notion-connect-light')
    await page.evaluate(() => window.localStorage.setItem('dsos.theme', 'dark'))

    // 6. the connected card (fixture: reaching it needs Notion)
    let fixture = connectedFixture()
    const patches = []
    const route = async (routed) => {
      const request = routed.request()
      if (request.method() === 'GET') return routed.fulfill({ json: fixture })
      if (request.method() === 'PATCH') {
        const wanted = JSON.parse(request.postData() || '{}').kinds ?? []
        patches.push(wanted)
        fixture = { ...fixture, kinds: fixture.kinds.map((item) => ({ ...item, enabled: wanted.includes(item.kind) })) }
        return routed.fulfill({ json: fixture })
      }
      return routed.continue()
    }
    await page.route('**/api/v1/integrations/notion', route)
    await page.reload({ waitUntil: 'networkidle' })
    await page.getByTestId('notion-kinds').waitFor({ timeout: 10_000 }).catch(() => {})
    check(
      (await page.getByTestId('notion-card').getAttribute('data-state')) === 'connected',
      'connected: the card says so',
    )
    const pageLink = page.getByTestId('notion-page-link')
    check(
      (await pageLink.getAttribute('href'))?.startsWith('https://app.notion.com/p/') && (await hasText('DisciplineOS')),
      'connected: the page title with «Открыть в Notion»',
    )
    check(await hasText('Подключение «DisciplineOS» · Мой Notion'), 'connected: the connection and workspace names')
    const switches = page.getByTestId('notion-kinds').getByRole('switch')
    check((await switches.count()) === 5, 'five kinds with a switch each', String(await switches.count()))
    const switchHeights = []
    for (let index = 0; index < (await switches.count()); index += 1) switchHeights.push(await height(switches.nth(index)))
    check(switchHeights.every((value) => value >= 47.5), 'every switch row is a 48 px target', switchHeights.map(Math.round).join(' / '))
    const openLinks = page.getByTestId('notion-kinds').getByRole('link', { name: /Открыть/ })
    check((await openLinks.count()) === 4, '«Открыть в Notion» for each database that exists', String(await openLinks.count()))
    check(
      (await page.getByTestId('notion-kind-days').getAttribute('data-database')) === 'missing' &&
        (await hasText('База удалена или недоступна в Notion')),
      'a database deleted in Notion is said so',
    )
    check(await hasText('Создать заново'), '«Создать заново» is offered for it')
    check(
      (await hasText('Последняя синхронизация')) && (await hasText('Создано: 3 · обновлено: 2 · в корзину: 1')),
      'the last run in words (when, created / updated / trashed)',
    )
    check(await hasText('Сервер синхронизирует сам каждые 10 мин'), 'the automatic interval is stated')
    const journal = page.getByTestId('notion-kind-journal').getByRole('switch')
    check((await journal.getAttribute('aria-checked')) === 'false', 'a switched-off kind shows off')
    await journal.click()
    await page.waitForTimeout(400)
    check(
      (await journal.getAttribute('aria-checked')) === 'true' && patches.at(-1)?.includes('journal'),
      'a switch saves the kinds (PATCH /integrations/notion)',
      JSON.stringify(patches.at(-1)),
    )
    await shot('63-notion-connected')
    await page.getByTestId('notion-disconnect').click()
    await page.waitForTimeout(500)
    check(
      await hasText('Всё, что уже скопировано в Notion, останется там'),
      '«Отключить» asks first and says the Notion data stays',
    )
    await page.getByRole('dialog').getByRole('button', { name: /^(Отмена|Cancel)$/ }).click()
    await page.waitForTimeout(400)
    check((await page.getByRole('dialog').count()) === 0, 'cancel keeps the connection')

    // 7. an error that needs the owner: in words, with the way out
    fixture = connectedFixture({ status: 'error', error: 'notion_unauthorized' })
    await page.reload({ waitUntil: 'networkidle' })
    await page.getByTestId('notion-card').waitFor({ timeout: 10_000 }).catch(() => {})
    check(await hasText('Notion не принял секрет'), 'error state: the reason in plain words')
    await page.getByRole('button', { name: 'Ввести новый секрет' }).click()
    await page.waitForTimeout(300)
    const replaceSecret = page.locator('input[name="notion-secret"]')
    check(
      (await replaceSecret.count()) === 1 &&
        (await replaceSecret.inputValue()) === '' &&
        (await page.locator('input[name="notion-page"]').inputValue()).startsWith('https://app.notion.com/p/'),
      'a new secret can be entered for the same page (the field starts empty)',
    )
    await page.evaluate(() => window.localStorage.setItem('dsos.theme', 'light'))
    fixture = connectedFixture()
    await page.reload({ waitUntil: 'networkidle' })
    await page.getByTestId('notion-kinds').waitFor({ timeout: 10_000 }).catch(() => {})
    await shot('64-notion-connected-light')
    await page.evaluate(() => window.localStorage.setItem('dsos.theme', 'dark'))
    await page.unroute('**/api/v1/integrations/notion', route)
    await page.reload({ waitUntil: 'networkidle' })
    await page.getByTestId('notion-card').waitFor({ timeout: 10_000 }).catch(() => {})
    check(
      (await page.getByTestId('notion-card').getAttribute('data-state')) === 'disconnected' &&
        (await page.evaluate(() => document.documentElement.dataset.theme)) === 'dark',
      'back to the real state (not connected) and the dark theme',
    )
    check(await nothingStored(), 'still no secret anywhere in the page or storage')
    ok('stage 9 Notion checks done')
  } finally {
    page.off('request', onRequest)
    await page.unroute('**/api/v1/integrations/notion').catch(() => {})
    await page.evaluate(() => window.localStorage.setItem('dsos.theme', 'dark')).catch(() => {})
  }
}
