#!/usr/bin/env node
/**
 * Bundle composition report (stage 9 — performance pass).
 *
 * Builds the web app once more (into a temporary directory) and attributes the bytes
 * of every emitted JS chunk to the modules in it: each module's rendered code (what
 * Rollup put into the chunk after tree-shaking) is minified on its own with the
 * build's esbuild, and the chunk's real size is split in those proportions. (Source
 * maps cannot do this: Vite's JSON modules — the locales — come with empty maps.)
 * Sizes are UTF-8 bytes; `vite build` prints string lengths, which read smaller for
 * Cyrillic text — both are shown for the main chunk. Prints:
 *
 *   - every JS chunk and CSS file: raw and gzip size, and whether it loads at start
 *     (the entry chunk and everything it imports statically — before the first render);
 *   - the main (entry) chunk by package and by source folder;
 *   - the start-up JS (all of it) by package.
 *
 * No dependency beyond the app's own Vite (and its esbuild).
 *
 * Usage:
 *   node scripts/perf/bundle-report.mjs [--json out.json] [--check] [--top 25]
 *     --check   exit 1 when the main chunk is above its budget (MAIN_BUDGET_KB, default 300)
 *               or when the start-up JS contains Recharts, src/features/stats code or
 *               framer-motion's animation features
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { gzipSync } from 'node:zlib'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const WEB = join(ROOT, 'web')
const args = process.argv.slice(2)
const flag = (name) => args.includes(name)
const option = (name, fallback) => {
  const index = args.indexOf(name)
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback
}
const TOP = Number(option('--top', '25'))
const JSON_OUT = option('--json', '') ? resolve(option('--json', '')) : '' // before the chdir below
const MAIN_BUDGET = Number(process.env.MAIN_BUDGET_KB ?? 300) * 1000
// never in the start-up JS (module paths relative to web/): the charts library and the
// stats dashboards are a lazy chunk; framer-motion's animation features load lazily
// (src/lib/motion.tsx) — a `motion.*` component anywhere at start would pull them back
const FORBIDDEN_AT_START = [
  /^node_modules\/(recharts|victory-vendor|d3-[a-z-]+)\//,
  /^src\/features\/stats\//,
  /^node_modules\/framer-motion\/dist\/es\/(motion\/features\/(animations|gestures|drag|layout)|render\/dom\/features-(max|animation))\b/,
]

const { build } = await import(pathToFileURL(join(WEB, 'node_modules/vite/dist/node/index.js')).href)
const esbuild = await import(pathToFileURL(join(WEB, 'node_modules/esbuild/lib/main.js')).href)
process.chdir(WEB) // tailwind's content globs are relative to the working directory

// --------------------------------------------------------------------------- //
// Build with a collector plugin
// --------------------------------------------------------------------------- //
const outDir = mkdtempSync(join(tmpdir(), 'dsos-bundle-'))
const chunks = []
const assets = []
const collector = {
  name: 'dsos-bundle-report',
  // writeBundle, not generateBundle: Vite's own post plugins run after every user plugin
  // and still rewrite the chunks there (the preload lists of dynamic imports) — only
  // here is chunk.code what lands in dist/
  writeBundle(_options, bundle) {
    for (const item of Object.values(bundle)) {
      if (item.type === 'chunk') {
        chunks.push({
          file: item.fileName,
          name: item.name,
          isEntry: item.isEntry,
          isDynamicEntry: item.isDynamicEntry,
          imports: item.imports,
          dynamicImports: item.dynamicImports,
          code: item.code,
          modules: Object.entries(item.modules).map(([id, module]) => ({ id, code: module.code ?? '' })),
        })
      } else if (item.fileName.endsWith('.css')) {
        assets.push({ file: item.fileName, source: Buffer.from(item.source) })
      }
    }
  },
}

const started = Date.now()
try {
  await build({
    root: WEB,
    configFile: join(WEB, 'vite.config.ts'),
    logLevel: 'warn',
    build: { outDir, emptyOutDir: true, reportCompressedSize: false },
    plugins: [collector],
  })
} finally {
  rmSync(outDir, { recursive: true, force: true })
}

// --------------------------------------------------------------------------- //
// Bytes per module: each module minified alone, the chunk split in proportion
// --------------------------------------------------------------------------- //
async function minifiedSize(code) {
  if (!code.trim()) return 0
  try {
    const result = await esbuild.transform(code, { minify: true, loader: 'js', target: 'es2022', format: 'esm' })
    return Buffer.byteLength(result.code)
  } catch {
    return Buffer.byteLength(code) // not parseable alone (rare): count it as rendered
  }
}

/** [{source, bytes}] for one chunk; the bytes add up to the chunk's real size. */
async function attribute(chunk) {
  const raw = Buffer.byteLength(chunk.code)
  const sizes = await Promise.all(chunk.modules.map((module) => minifiedSize(module.code)))
  const total = sizes.reduce((sum, size) => sum + size, 0)
  if (total === 0) return [{ source: '(chunk glue)', bytes: raw }]
  const glue = Math.max(0, raw - total) // imports/exports, the runtime wrapper (≈ 0 when ≥ the modules)
  const scale = (raw - glue) / total
  const rows = chunk.modules.map((module, index) => ({ source: module.id, bytes: sizes[index] * scale }))
  if (glue) rows.push({ source: '(chunk glue)', bytes: glue })
  return rows
}

/** A module id → { pkg, folder, file } (paths relative to web/). */
function classify(source) {
  if (source.startsWith('(')) return { pkg: source, folder: source, file: source }
  const clean = source.replace(/^\0/, '').replace(/\?.*$/, '')
  if (!clean.startsWith('/')) return { pkg: '(vite runtime)', folder: '(vite runtime)', file: clean }
  const rel = relative(WEB, clean).replaceAll('\\', '/')
  const nm = rel.lastIndexOf('node_modules/')
  if (nm >= 0) {
    const parts = rel.slice(nm + 'node_modules/'.length).split('/')
    const pkg = parts[0].startsWith('@') ? `${parts[0]}/${parts[1]}` : parts[0]
    return { pkg, folder: `node_modules/${pkg}`, file: rel.slice(nm) }
  }
  if (rel.startsWith('src/')) {
    const parts = rel.split('/')
    const depth = parts[1] === 'features' || parts[1] === 'components' || parts[1] === 'i18n' ? 3 : 2
    const folder = parts.slice(0, Math.min(depth, parts.length - 1)).join('/')
    return { pkg: '(app)', folder, file: rel }
  }
  return { pkg: '(vite runtime)', folder: '(vite runtime)', file: rel }
}

const gzip = (buffer) => gzipSync(buffer).length
const report = { builtInMs: Date.now() - started, chunks: [], css: [] }
for (const chunk of chunks) {
  const raw = Buffer.byteLength(chunk.code)
  const modules = (await attribute(chunk)).map(({ source, bytes }) => ({ ...classify(source), bytes }))
  report.chunks.push({
    file: chunk.file,
    name: chunk.name,
    isEntry: chunk.isEntry,
    isDynamicEntry: chunk.isDynamicEntry,
    imports: chunk.imports,
    dynamicImports: chunk.dynamicImports,
    raw,
    viteKb: chunk.code.length / 1000, // what `vite build` prints (string length)
    gzip: gzip(Buffer.from(chunk.code)),
    modules,
  })
}
for (const asset of assets) report.css.push({ file: asset.file, raw: asset.source.length, gzip: gzip(asset.source) })

// start-up = the entry chunk and its static import closure
const byFile = new Map(report.chunks.map((chunk) => [chunk.file, chunk]))
const entry = report.chunks.find((chunk) => chunk.isEntry)
const startup = new Set()
const visit = (file) => {
  if (startup.has(file) || !byFile.has(file)) return
  startup.add(file)
  byFile.get(file).imports.forEach(visit)
}
visit(entry.file)
for (const chunk of report.chunks) chunk.atStart = startup.has(chunk.file)
report.main = entry.file
report.startup = [...startup]

// --------------------------------------------------------------------------- //
// Print
// --------------------------------------------------------------------------- //
const kb = (bytes) => `${(bytes / 1000).toFixed(1)} kB`
const pad = (text, width) => String(text).padEnd(width)
const lpad = (text, width) => String(text).padStart(width)
const group = (modules, key) => {
  const totals = new Map()
  for (const module of modules) totals.set(module[key], (totals.get(module[key]) ?? 0) + module.bytes)
  return [...totals].sort((a, b) => b[1] - a[1])
}
const table = (title, rows, total) => {
  console.log(`\n${title}`)
  for (const [name, bytes] of rows.slice(0, TOP)) {
    console.log(`  ${pad(name, 52)} ${lpad(kb(bytes), 10)} ${lpad(((bytes / total) * 100).toFixed(1), 5)} %`)
  }
  if (rows.length > TOP) {
    const rest = rows.slice(TOP).reduce((sum, [, bytes]) => sum + bytes, 0)
    console.log(`  ${pad(`… ${rows.length - TOP} more`, 52)} ${lpad(kb(rest), 10)}`)
  }
}

const sorted = [...report.chunks].sort((a, b) => Number(b.atStart) - Number(a.atStart) || b.raw - a.raw)
console.log(`chunks (${report.chunks.length}; * = loaded at start)                         raw       gzip`)
for (const chunk of sorted) {
  console.log(`  ${chunk.atStart ? '*' : ' '} ${pad(chunk.file, 50)} ${lpad(kb(chunk.raw), 10)} ${lpad(kb(chunk.gzip), 10)}`)
}
for (const css of report.css) console.log(`  * ${pad(css.file, 50)} ${lpad(kb(css.raw), 10)} ${lpad(kb(css.gzip), 10)}`)

const start = report.chunks.filter((chunk) => chunk.atStart)
const startRaw = start.reduce((sum, chunk) => sum + chunk.raw, 0)
const startGzip = start.reduce((sum, chunk) => sum + chunk.gzip, 0)
const cssRaw = report.css.reduce((sum, css) => sum + css.raw, 0)
const cssGzip = report.css.reduce((sum, css) => sum + css.gzip, 0)
const mainChunk = byFile.get(entry.file)
report.totals = {
  mainRaw: mainChunk.raw,
  mainViteKb: mainChunk.viteKb,
  mainGzip: mainChunk.gzip,
  startupJsRaw: startRaw,
  startupJsGzip: startGzip,
  startupChunks: start.length,
  cssRaw,
  cssGzip,
  allJsRaw: report.chunks.reduce((sum, chunk) => sum + chunk.raw, 0),
}
console.log(
  `\nmain chunk ${entry.file}: ${kb(mainChunk.raw)} raw (vite prints ${mainChunk.viteKb.toFixed(2)} kB), ${kb(mainChunk.gzip)} gzip` +
    `\nstart-up JS: ${start.length} chunks, ${kb(startRaw)} raw, ${kb(startGzip)} gzip; CSS ${kb(cssRaw)} raw, ${kb(cssGzip)} gzip`,
)
table(`main chunk by package (${entry.file})`, group(mainChunk.modules, 'pkg'), mainChunk.raw)
table('main chunk by source folder', group(mainChunk.modules, 'folder'), mainChunk.raw)
table('main chunk: largest modules', group(mainChunk.modules, 'file'), mainChunk.raw)
table('start-up JS by package (all chunks loaded at start)', group(start.flatMap((chunk) => chunk.modules), 'pkg'), startRaw)

if (JSON_OUT) {
  const out = JSON_OUT
  const slim = { ...report, chunks: report.chunks.map(({ modules, ...rest }) => ({ ...rest, packages: group(modules, 'pkg'), folders: group(modules, 'folder') })) }
  writeFileSync(out, JSON.stringify(slim, null, 2))
  console.log(`\njson → ${out}`)
}

if (flag('--check')) {
  const problems = []
  if (mainChunk.raw > MAIN_BUDGET) problems.push(`main chunk ${kb(mainChunk.raw)} > budget ${kb(MAIN_BUDGET)}`)
  for (const chunk of start) {
    for (const module of chunk.modules) {
      if (FORBIDDEN_AT_START.some((pattern) => pattern.test(module.file))) {
        problems.push(`${module.file} is loaded at start (${chunk.file})`)
      }
    }
  }
  if (problems.length) {
    console.log(`\nCHECK FAILED\n  ${[...new Set(problems)].slice(0, 20).join('\n  ')}`)
    process.exit(1)
  }
  console.log(`\ncheck ok: main chunk ≤ ${kb(MAIN_BUDGET)}; no charts, stats or motion features at start`)
}
