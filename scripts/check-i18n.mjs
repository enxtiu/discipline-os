#!/usr/bin/env node
/**
 * i18n completeness gate.
 *
 * Every product string has to go through i18next and both bundled locales have
 * to stay in sync — otherwise the UI silently renders a raw key ("goal.lose")
 * instead of a word. Dependency-free, runs in CI:
 *
 *   node scripts/check-i18n.mjs            # fail on missing keys / drift
 *   node scripts/check-i18n.mjs --unused   # also fail on keys nobody uses
 *
 * What counts as "used":
 *   - literal calls            t('today.title'), t(`plan.empty`)
 *   - indirect keys            const STEP_KEYS = ['onboarding.step_body', …]
 *                              { labelKey: 'food.photo' } → t(action.labelKey)
 *   - dynamic prefixes         t(`nav.${labelKey}`), `errors.${code}` → the
 *                              prefix must exist in the locale
 * A dotted string only counts when its first segment is a real locale section,
 * so module paths and URLs are not mistaken for translation keys.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const WEB_SRC = join(ROOT, 'web', 'src')
const LOCALES = join(WEB_SRC, 'i18n', 'locales')
const FAIL_ON_UNUSED = process.argv.includes('--unused')

// `ru` is the default locale, so it is the reference the others are diffed against.
const LOCALE_FILES = readdirSync(LOCALES)
  .filter((name) => name.endsWith('.json'))
  .sort((a, b) => (a.startsWith('ru') ? -1 : b.startsWith('ru') ? 1 : a.localeCompare(b)))

function flatten(value, prefix = '', into = new Map()) {
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (child && typeof child === 'object' && !Array.isArray(child)) flatten(child, path, into)
    else into.set(path, child)
  }
  return into
}

function* walk(directory) {
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) {
      if (entry === 'node_modules' || entry === 'generated') continue
      yield* walk(path)
    } else if (/\.(ts|tsx)$/.test(entry)) {
      yield path
    }
  }
}

const locales = new Map()
for (const name of LOCALE_FILES) {
  const code = name.replace(/\.json$/, '')
  locales.set(code, flatten(JSON.parse(readFileSync(join(LOCALES, name), 'utf8'))))
}
const reference = LOCALE_FILES[0].replace(/\.json$/, '')
const referenceKeys = locales.get(reference)
const sections = new Set([...referenceKeys.keys()].map((key) => key.split('.')[0]))

const LITERAL = /\bt\(\s*'([a-zA-Z0-9_.-]+)'/g
const TEMPLATE = /\bt\(\s*`([a-zA-Z0-9_.-]+)`/g
const DOTTED = /['"`]([a-zA-Z0-9_-]+\.[a-zA-Z0-9_.-]+)['"`]/g
const PREFIXES = /`([a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*)\.\$\{/g

const used = new Set()
const dynamicPrefixes = new Set()
const files = [...walk(WEB_SRC)]
for (const file of files) {
  const source = readFileSync(file, 'utf8')
  for (const match of source.matchAll(LITERAL)) used.add(match[1])
  for (const match of source.matchAll(TEMPLATE)) used.add(match[1])
  for (const match of source.matchAll(DOTTED)) {
    if (sections.has(match[1].split('.')[0])) used.add(match[1])
  }
  for (const match of source.matchAll(PREFIXES)) {
    if (sections.has(match[1].split('.')[0])) dynamicPrefixes.add(match[1])
  }
}

const problems = []

// 1. every key used in code exists in every locale
for (const [code, keys] of locales) {
  const missing = [...used].filter((key) => !keys.has(key)).sort()
  if (missing.length) {
    problems.push(`${code}: missing ${missing.length} key(s) used in code → ${missing.join(', ')}`)
  }
}

// 2. locales do not drift apart
for (const [code, keys] of locales) {
  if (code === reference) continue
  const absent = [...referenceKeys.keys()].filter((key) => !keys.has(key)).sort()
  const extra = [...keys.keys()].filter((key) => !referenceKeys.has(key)).sort()
  if (absent.length) {
    problems.push(`${code}: ${absent.length} key(s) present in ${reference} but missing here → ${absent.join(', ')}`)
  }
  if (extra.length) problems.push(`${code}: ${extra.length} key(s) not present in ${reference} → ${extra.join(', ')}`)
}

// 3. every dynamic prefix resolves (a typo'd prefix renders raw keys at runtime)
for (const prefix of dynamicPrefixes) {
  const present = [...referenceKeys.keys()].some((key) => key.startsWith(`${prefix}.`))
  if (!present) problems.push(`${reference}: no keys under the dynamic prefix \`${prefix}.\``)
}

// 4. no placeholder translations
for (const [code, keys] of locales) {
  for (const [key, value] of keys) {
    if (typeof value === 'string' && (!value.trim() || value === key)) {
      problems.push(`${code}: "${key}" has no real translation (${JSON.stringify(value)})`)
    }
  }
}

const unused = [...referenceKeys.keys()].filter(
  (key) => !used.has(key) && ![...dynamicPrefixes].some((prefix) => key.startsWith(`${prefix}.`)),
)

console.log(
  `i18n: ${LOCALE_FILES.join(', ')} · ${referenceKeys.size} keys in ${reference} · ` +
    `${used.size} key(s) referenced in code · ${dynamicPrefixes.size} dynamic prefix(es)`,
)
console.log(`scanned ${relative(ROOT, WEB_SRC)} (${files.length} ts/tsx files)`)

if (unused.length) {
  console.log(`\nnote: ${unused.length} key(s) in ${reference} are not referenced (reserved for later stages)`)
  if (FAIL_ON_UNUSED) problems.push(`${unused.length} unused key(s): ${unused.slice(0, 20).join(', ')}`)
}

if (problems.length) {
  console.error(`\n${problems.length} i18n problem(s):`)
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log('\ni18n is complete and in sync.')
