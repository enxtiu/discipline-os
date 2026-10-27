#!/usr/bin/env node
/**
 * Load-performance run (stage 9 — performance pass).
 *
 * Opens screens of the production build (`npm run build` + `vite preview`, API behind
 * its proxy) in Chromium the way a phone on mobile data would, and records per load:
 *
 *   js / css / api   bytes transferred (encoded: gzip as served) and request counts
 *   requests         every request until the network is idle (no request for 1 s)
 *   fcp              first contentful paint (the boot spinner in index.html)
 *   shell            the app shell painted (header + tab bar: JS ran, session loaded)
 *   ring             Today only: the calorie ring painted (the day's data is in)
 *   ready            the screen painted with data: its marker present, no skeleton left
 *   lcp              largest contentful paint (Chromium's own measure)
 *   idle             the last request finished (the network then stays quiet for 1 s)
 *   long tasks       count, total ms and blocking ms (Σ duration − 50) until idle
 *
 * Conditions: iPhone 13 viewport (390×844), ru-RU, Europe/Moscow; CDP CPU throttling ×4
 * and DevTools' "Fast 4G" profile (165 ms latency, 9 Mbit/s × 0.9 down, 1.5 Mbit/s × 0.9
 * up). Two modes:
 *   cold  a fresh browser context per load (empty HTTP cache, service worker blocked) —
 *         the first open on mobile data;
 *   warm  the installed app opened again: the service worker has precached the build on
 *         a first visit, the measured load is the second one (JS from the SW cache, API
 *         over the network).
 * Each (mode, screen) is loaded --runs times (default 3); the summary shows medians.
 *
 * --tabs adds a tab-switching run per mode: open /today, then tap Food, Plan, Today,
 * Food, Plan, Stats, Today in the tab bar (each once the previous screen is ready and
 * the network idle) and count the API requests and the time to "ready" of every switch.
 *
 * Usage:
 *   DEVICE_TOKEN=… | PAIRING_CODE=XXXX-XXXX \
 *   WEB_URL=http://127.0.0.1:4236 node scripts/perf/perf-run.mjs \
 *     [--runs 3] [--modes cold,warm] [--screens today,food,plan,stats] [--json out.json] [--label before]
 * PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs when playwright is not resolvable.
 */
const { chromium, devices } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright')
const { writeFileSync } = await import('node:fs')

const args = process.argv.slice(2)
const option = (name, fallback) => {
  const index = args.indexOf(name)
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback
}
const WEB_URL = (process.env.WEB_URL ?? 'http://127.0.0.1:4236').replace(/\/$/, '')
const RUNS = Number(option('--runs', '3'))
const MODES = option('--modes', 'cold,warm').split(',')
const LABEL = option('--label', '')
const JSON_OUT = option('--json', '')
const QUIET_MS = 1000
const TIMEOUT_MS = 45_000

// DevTools "Fast 4G" (front_end/core/sdk/NetworkManager.ts) and a mid-range phone CPU
const NETWORK = {
  offline: false,
  latency: 60 * 2.75,
  downloadThroughput: ((9 * 1000 * 1000) / 8) * 0.9,
  uploadThroughput: ((1.5 * 1000 * 1000) / 8) * 0.9,
}
const CPU_RATE = 4

/** Per screen: the path, the marker of "painted with data", and named extra marks. */
const SCREENS = {
  today: { path: '/today', ready: 'main [role="progressbar"]', marks: { ring: 'main [role="progressbar"][style*="196px"]' } },
  food: { path: '/food', ready: 'main h1, main h2' },
  plan: { path: '/plan', ready: '[data-testid="plan-today"]' },
  stats: { path: '/stats', ready: 'main h1' },
}
const SELECTED = option('--screens', Object.keys(SCREENS).join(',')).split(',')
const TABS = args.includes('--tabs')
const TAB_SEQUENCE = ['food', 'plan', 'today', 'food', 'plan', 'stats', 'today']

const iPhone = devices['iPhone 13']
const browser = await chromium.launch()

// --------------------------------------------------------------------------- //
// Auth: one device token for every load (pairing reuses the device by fingerprint)
// --------------------------------------------------------------------------- //
let TOKEN = process.env.DEVICE_TOKEN ?? ''
if (!TOKEN) {
  const code = process.env.PAIRING_CODE ?? ''
  if (!code) {
    console.error('DEVICE_TOKEN or PAIRING_CODE is required (python -m app.cli pairing-code --uses 20 --ttl 1440)')
    process.exit(2)
  }
  const response = await fetch(`${WEB_URL}/api/v1/auth/pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code,
      device: { name: 'Perf run', platform: 'ios', model: 'iPhone', app_version: 'perf', fingerprint: 'fp_perf_run' },
      timezone: 'Europe/Moscow',
      language: 'ru',
    }),
  })
  if (!response.ok) {
    console.error(`pairing failed: HTTP ${response.status} ${await response.text()}`)
    process.exit(2)
  }
  TOKEN = (await response.json()).token
  console.log(`paired; reuse with DEVICE_TOKEN=${TOKEN}`)
}

// --------------------------------------------------------------------------- //
// In-page probe: paint marks, long tasks, LCP (installed before any app script)
// --------------------------------------------------------------------------- //
function probe({ token, selectors }) {
  try {
    localStorage.setItem('dsos.device_token', token)
    localStorage.setItem('dsos.language', 'ru')
  } catch {
    /* ignore */
  }
  const perf = { marks: {}, longtasks: [], lcp: 0 }
  window.__perf = perf
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) perf.longtasks.push([entry.startTime, entry.duration])
    }).observe({ type: 'longtask', buffered: true })
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) perf.lcp = entry.startTime
    }).observe({ type: 'largest-contentful-paint', buffered: true })
  } catch {
    /* ignore */
  }
  const painted = (name) => {
    // DOM time now; the paint is the next frame (rAF runs before it, the task after it)
    perf.marks[`${name}Dom`] = performance.now()
    requestAnimationFrame(() => setTimeout(() => (perf.marks[name] = performance.now()), 0))
  }
  const check = () => {
    for (const [name, selector] of Object.entries(selectors)) {
      if (`${name}Dom` in perf.marks) continue
      if (name === 'ready') {
        const main = document.querySelector('main')
        if (!main || !document.querySelector(selector) || main.querySelector('.skeleton')) continue
      } else if (!document.querySelector(selector)) continue
      painted(name)
    }
  }
  const observer = new MutationObserver(check)
  const start = () => observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true })
  if (document.documentElement) start()
  else document.addEventListener('DOMContentLoaded', start)
}

// --------------------------------------------------------------------------- //
// One measured load
// --------------------------------------------------------------------------- //
async function newContext(mode) {
  return browser.newContext({
    ...iPhone,
    viewport: { width: 390, height: 844 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    serviceWorkers: mode === 'cold' ? 'block' : 'allow',
  })
}

async function throttle(page) {
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.emulateNetworkConditions', NETWORK)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: CPU_RATE })
  return cdp
}

/** Warm mode: a first visit installs the service worker and lets it precache the build. */
async function installServiceWorker(page, screen) {
  await page.goto(`${WEB_URL}${screen.path}`, { waitUntil: 'networkidle' })
  const ok = await page.evaluate(async () => {
    if (!('serviceWorker' in navigator)) return false
    const registration = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((resolve) => setTimeout(() => resolve(null), 20_000)),
    ])
    return !!registration?.active
  })
  if (!ok) throw new Error('service worker did not activate')
  await page.waitForTimeout(1500) // precache finished (install precedes activate) and the page settled
}

async function measure(mode, name) {
  const screen = SCREENS[name]
  const context = await newContext(mode)
  const selectors = { shell: 'nav.tabbar', ready: screen.ready, ...(screen.marks ?? {}) }
  await context.addInitScript(probe, { token: TOKEN, selectors })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (error) => errors.push(String(error)))
  page.on('console', (message) => message.type() === 'error' && errors.push(message.text()))

  if (mode === 'warm') await installServiceWorker(page, screen)

  const cdp = await throttle(page)
  const requests = new Map() // requestId → { url, type, start, end, bytes, fromSW }
  let navStart = null
  let lastActivity = Date.now()
  cdp.on('Network.requestWillBeSent', (event) => {
    if (navStart === null && event.type === 'Document') navStart = event.timestamp
    if (navStart === null) return
    requests.set(event.requestId, { url: event.request.url, method: event.request.method, start: event.timestamp, type: event.type })
    lastActivity = Date.now()
  })
  cdp.on('Network.responseReceived', (event) => {
    const item = requests.get(event.requestId)
    if (!item) return
    item.type = event.type
    item.status = event.response.status
    item.fromSW = !!event.response.fromServiceWorker
    item.fromCache = !!event.response.fromDiskCache
  })
  const finish = (event, failed) => {
    const item = requests.get(event.requestId)
    if (!item) return
    item.end = event.timestamp
    item.bytes = failed ? 0 : event.encodedDataLength
    item.failed = failed
    lastActivity = Date.now()
  }
  cdp.on('Network.loadingFinished', (event) => finish(event, false))
  cdp.on('Network.loadingFailed', (event) => finish(event, true))

  const startedAt = Date.now()
  await page.goto(`${WEB_URL}${screen.path}`, { waitUntil: 'commit', timeout: TIMEOUT_MS })
  // idle: nothing in flight and no activity for QUIET_MS (after the ready mark, so slow
  // CPU work between requests is not mistaken for idleness)
  for (;;) {
    await page.waitForTimeout(100)
    const inflight = [...requests.values()].some((item) => item.end === undefined)
    const ready = await page.evaluate(() => 'ready' in (window.__perf?.marks ?? {})).catch(() => false)
    if (!inflight && ready && Date.now() - lastActivity >= QUIET_MS) break
    if (Date.now() - startedAt > TIMEOUT_MS) {
      errors.push(`timeout: ready=${ready} inflight=${inflight}`)
      break
    }
  }
  const page_ = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0]
    const fcp = performance.getEntriesByName('first-contentful-paint')[0]
    return {
      marks: window.__perf.marks,
      longtasks: window.__perf.longtasks,
      lcp: window.__perf.lcp,
      fcp: fcp ? fcp.startTime : null,
      dcl: nav ? nav.domContentLoadedEventEnd : null,
      load: nav ? nav.loadEventEnd : null,
    }
  })
  await context.close()

  const all = [...requests.values()].filter((item) => item.end !== undefined)
  const ms = (timestamp) => Math.round((timestamp - navStart) * 1000)
  const isApi = (item) => new URL(item.url).pathname.startsWith('/api/')
  const sum = (items) => items.reduce((total, item) => total + (item.bytes ?? 0), 0)
  const js = all.filter((item) => item.type === 'Script')
  const css = all.filter((item) => item.type === 'Stylesheet')
  const api = all.filter(isApi)
  const blocking = page_.longtasks.reduce((total, [, duration]) => total + Math.max(0, duration - 50), 0)
  return {
    mode,
    screen: name,
    fcp: page_.fcp && Math.round(page_.fcp),
    shell: page_.marks.shell && Math.round(page_.marks.shell),
    ring: page_.marks.ring && Math.round(page_.marks.ring),
    ready: page_.marks.ready && Math.round(page_.marks.ready),
    lcp: page_.lcp && Math.round(page_.lcp),
    idle: all.length ? Math.max(...all.map((item) => ms(item.end))) : null,
    requests: all.length,
    jsRequests: js.length,
    jsBytes: sum(js),
    jsFromSW: js.filter((item) => item.fromSW).length,
    cssBytes: sum(css),
    apiRequests: api.length,
    apiBytes: sum(api),
    longTasks: page_.longtasks.length,
    longTaskMs: Math.round(page_.longtasks.reduce((total, [, duration]) => total + duration, 0)),
    blockingMs: Math.round(blocking),
    apiCalls: api.map((item) => `${item.method} ${new URL(item.url).pathname}${new URL(item.url).search}`),
    scripts: js.map((item) => new URL(item.url).pathname.replace('/assets/', '')),
    errors,
  }
}

// --------------------------------------------------------------------------- //
// Tab switching: API requests and time to ready per tap, after a cold open
// --------------------------------------------------------------------------- //
async function tabs(mode) {
  const context = await newContext(mode)
  const selectors = { shell: 'nav.tabbar', ready: SCREENS.today.ready }
  await context.addInitScript(probe, { token: TOKEN, selectors })
  const page = await context.newPage()
  if (mode === 'warm') await installServiceWorker(page, SCREENS.today)
  const cdp = await throttle(page)
  const inflight = new Set()
  let calls = []
  let lastActivity = Date.now()
  cdp.on('Network.requestWillBeSent', (event) => {
    inflight.add(event.requestId)
    lastActivity = Date.now()
    const url = new URL(event.request.url)
    if (url.pathname.startsWith('/api/')) calls.push(`${event.request.method} ${url.pathname}${url.search}`)
  })
  const done = (event) => {
    inflight.delete(event.requestId)
    lastActivity = Date.now()
  }
  cdp.on('Network.loadingFinished', done)
  cdp.on('Network.loadingFailed', done)
  const settle = async (selector, started = Date.now()) => {
    let readyAt = null
    for (;;) {
      await page.waitForTimeout(50)
      if (readyAt === null) {
        const ready = await page
          .evaluate((sel) => {
            const main = document.querySelector('main')
            return !!main && !!document.querySelector(sel) && !main.querySelector('.skeleton')
          }, selector)
          .catch(() => false)
        if (ready) readyAt = Date.now()
      }
      if (readyAt !== null && inflight.size === 0 && Date.now() - lastActivity >= QUIET_MS) break
      if (Date.now() - started > TIMEOUT_MS) break
    }
    return readyAt === null ? null : readyAt - started
  }
  await page.goto(`${WEB_URL}/today`, { waitUntil: 'commit' })
  await settle(SCREENS.today.ready)
  const switches = []
  for (const name of TAB_SEQUENCE) {
    calls = []
    const started = Date.now()
    // the Stats button sits under the raised quick-add button: dispatch the tap on it directly
    if (name === 'stats') await page.locator('nav.tabbar button.tab').first().dispatchEvent('click')
    else await page.locator(`nav.tabbar a[href="${SCREENS[name].path}"]`).click()
    const readyMs = await settle(SCREENS[name].ready, started) // from the tap
    switches.push({ screen: name, readyMs, apiCalls: calls })
  }
  await context.close()
  return switches
}

// --------------------------------------------------------------------------- //
const results = []
for (const mode of MODES) {
  for (const name of SELECTED) {
    for (let run = 1; run <= RUNS; run += 1) {
      const result = await measure(mode, name)
      results.push({ ...result, run })
      console.log(
        `${mode.padEnd(4)} ${name.padEnd(5)} #${run}  shell ${result.shell} ms · ready ${result.ready} ms` +
          `${result.ring ? ` · ring ${result.ring} ms` : ''} · idle ${result.idle} ms · ${result.requests} req` +
          ` · js ${(result.jsBytes / 1000).toFixed(1)} kB${result.errors.length ? ` · ERRORS ${result.errors.length}` : ''}`,
      )
    }
  }
}
const tabRuns = []
if (TABS) {
  for (const mode of MODES) {
    for (let run = 1; run <= RUNS; run += 1) {
      const switches = await tabs(mode)
      tabRuns.push({ mode, run, switches })
      console.log(
        `${mode.padEnd(4)} tabs  #${run}  ` +
          switches.map((item) => `${item.screen} ${item.apiCalls.length} req/${item.readyMs} ms`).join(' · '),
      )
    }
  }
}
await browser.close()

// --------------------------------------------------------------------------- //
// Summary: medians per mode and screen
// --------------------------------------------------------------------------- //
const median = (values) => {
  const sorted = values.filter((value) => typeof value === 'number').sort((a, b) => a - b)
  if (!sorted.length) return null
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : Math.round((sorted[middle - 1] + sorted[middle]) / 2)
}
const FIELDS = ['fcp', 'shell', 'ring', 'ready', 'lcp', 'idle', 'requests', 'jsRequests', 'jsBytes', 'cssBytes', 'apiRequests', 'apiBytes', 'longTasks', 'longTaskMs', 'blockingMs']
const summary = []
for (const mode of MODES) {
  for (const name of SELECTED) {
    const runs = results.filter((result) => result.mode === mode && result.screen === name)
    const row = { mode, screen: name, runs: runs.length }
    for (const field of FIELDS) row[field] = median(runs.map((result) => result[field]))
    row.apiCalls = runs[0]?.apiCalls ?? []
    row.scripts = runs[0]?.scripts ?? []
    row.errors = runs.flatMap((result) => result.errors)
    summary.push(row)
  }
}

const kb = (bytes) => (bytes == null ? '—' : (bytes / 1000).toFixed(1))
const v = (value) => (value == null ? '—' : String(value))
console.log(`\nmedians of ${RUNS} runs${LABEL ? ` (${LABEL})` : ''} — CPU ×${CPU_RATE}, Fast 4G; times in ms from navigation start, bytes in kB as transferred`)
console.log('mode screen   fcp  shell   ring  ready    lcp   idle  req  js-req  js-kB  css-kB  api-req  api-kB  long  long-ms  block-ms')
for (const row of summary) {
  console.log(
    [
      row.mode.padEnd(4),
      row.screen.padEnd(6),
      v(row.fcp).padStart(5),
      v(row.shell).padStart(6),
      v(row.ring).padStart(6),
      v(row.ready).padStart(6),
      v(row.lcp).padStart(6),
      v(row.idle).padStart(6),
      v(row.requests).padStart(4),
      v(row.jsRequests).padStart(7),
      kb(row.jsBytes).padStart(6),
      kb(row.cssBytes).padStart(7),
      v(row.apiRequests).padStart(8),
      kb(row.apiBytes).padStart(7),
      v(row.longTasks).padStart(5),
      v(row.longTaskMs).padStart(8),
      v(row.blockingMs).padStart(9),
    ].join(' '),
  )
}
for (const row of summary.filter((item) => item.mode === MODES[0])) {
  console.log(`\n${row.screen}: ${row.apiCalls.length} API calls — ${row.apiCalls.join(', ')}`)
  console.log(`  scripts: ${row.scripts.join(', ')}`)
}
const tabSummary = []
for (const mode of TABS ? MODES : []) {
  const runs = tabRuns.filter((item) => item.mode === mode)
  const steps = TAB_SEQUENCE.map((screen, index) => ({
    screen,
    apiRequests: median(runs.map((item) => item.switches[index].apiCalls.length)),
    readyMs: median(runs.map((item) => item.switches[index].readyMs)),
    apiCalls: runs[0]?.switches[index].apiCalls ?? [],
  }))
  const total = median(runs.map((item) => item.switches.reduce((sum, step) => sum + step.apiCalls.length, 0)))
  tabSummary.push({ mode, steps, totalApiRequests: total })
  console.log(`\ntab switches (${mode}, medians): ${steps.map((step) => `${step.screen} ${step.apiRequests} req / ${step.readyMs} ms`).join(' → ')}; ${total} API requests in all`)
  for (const step of steps) console.log(`  → ${step.screen}: ${step.apiCalls.join(', ') || '(none)'}`)
}
const errors = summary.flatMap((row) => row.errors.map((error) => `${row.mode} ${row.screen}: ${error}`))
if (errors.length) console.log(`\nerrors (${errors.length}):\n  ${[...new Set(errors)].slice(0, 20).join('\n  ')}`)

if (JSON_OUT) {
  writeFileSync(JSON_OUT, JSON.stringify({ label: LABEL, runs: RUNS, network: NETWORK, cpu: CPU_RATE, summary, results, tabSummary, tabRuns }, null, 2))
  console.log(`\njson → ${JSON_OUT}`)
}
