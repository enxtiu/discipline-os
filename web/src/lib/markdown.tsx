/**
 * A small, safe Markdown renderer for notes (tasks, projects, resources, the
 * journal). It builds React elements — never `innerHTML` — so a note can never
 * inject markup or script, and only `http(s):` / `mailto:` links are clickable.
 *
 * Supported: `#`–`######` headings, paragraphs (a single newline is a line
 * break — notes are typed on a phone), `-`/`*`/`+` and `1.` lists with one
 * nesting level, task items `- [ ]` / `- [x]` (optionally toggleable),
 * `>` quotes, fenced code blocks, `---` rules, and inline `**bold**`,
 * `*italic*`/`_italic_`, `~~strike~~`, `` `code` ``, `[text](url)` and bare URLs.
 */
import { Fragment, type ReactNode } from 'react'
import clsx from 'clsx'

const SAFE_URL = /^(https?:\/\/|mailto:)/i

type Inline = { pattern: RegExp; render: (match: RegExpExecArray, key: string) => ReactNode }

const INLINE: Inline[] = [
  {
    pattern: /`([^`\n]+)`/,
    render: (m, key) => (
      <code key={key} className="rounded-md bg-[rgb(var(--bg-sunken))] px-1.5 py-0.5 font-mono text-[0.9em]">
        {m[1]}
      </code>
    ),
  },
  {
    pattern: /\[([^\]\n]+)\]\(([^)\s]+)\)/,
    render: (m, key) =>
      SAFE_URL.test(m[2]) ? (
        <a
          key={key}
          href={m[2]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[rgb(var(--accent))] underline underline-offset-2"
        >
          {renderInline(m[1], key)}
        </a>
      ) : (
        <Fragment key={key}>{m[0]}</Fragment>
      ),
  },
  {
    pattern: /(https?:\/\/[^\s<]+[^\s<.,:;"')\]!?])/,
    render: (m, key) => (
      <a
        key={key}
        href={m[1]}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all text-[rgb(var(--accent))] underline underline-offset-2"
      >
        {m[1]}
      </a>
    ),
  },
  { pattern: /\*\*([^*\n]+)\*\*/, render: (m, key) => <strong key={key}>{renderInline(m[1], key)}</strong> },
  { pattern: /__([^_\n]+)__/, render: (m, key) => <strong key={key}>{renderInline(m[1], key)}</strong> },
  { pattern: /~~([^~\n]+)~~/, render: (m, key) => <s key={key}>{renderInline(m[1], key)}</s> },
  { pattern: /\*([^*\n]+)\*/, render: (m, key) => <em key={key}>{renderInline(m[1], key)}</em> },
  { pattern: /(?<![\p{L}\p{N}])_([^_\n]+)_(?![\p{L}\p{N}])/u, render: (m, key) => <em key={key}>{renderInline(m[1], key)}</em> },
]

/** Inline markup → React nodes (earliest match first, recursion for nested emphasis). */
export function renderInline(text: string, keyPrefix = 'i'): ReactNode[] {
  const out: ReactNode[] = []
  let rest = text
  let index = 0
  while (rest.length > 0) {
    let best: { match: RegExpExecArray; rule: Inline } | null = null
    for (const rule of INLINE) {
      const match = rule.pattern.exec(rest)
      if (match && (best === null || match.index < best.match.index)) best = { match, rule }
    }
    if (!best) {
      out.push(rest)
      break
    }
    if (best.match.index > 0) out.push(rest.slice(0, best.match.index))
    out.push(best.rule.render(best.match, `${keyPrefix}.${index++}`))
    rest = rest.slice(best.match.index + best.match[0].length)
  }
  return out
}

interface ListItem {
  text: string
  line: number
  checked: boolean | null // null = not a task item
  children: ListItem[]
}
interface ListBlock {
  ordered: boolean
  items: ListItem[]
}

const LIST_LINE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/
const TASK_PREFIX = /^\[( |x|X)\]\s+(.*)$/

function parseListItem(content: string, line: number): ListItem {
  const task = TASK_PREFIX.exec(content)
  return task
    ? { text: task[2], line, checked: task[1].toLowerCase() === 'x', children: [] }
    : { text: content, line, checked: null, children: [] }
}

export function Markdown({
  source,
  className,
  onToggleTask,
}: {
  source: string | null | undefined
  className?: string
  /** Makes `- [ ]` items tappable; receives the 0-based source line and the new state. */
  onToggleTask?: (line: number, checked: boolean) => void
}) {
  const text = (source ?? '').replace(/\r\n?/g, '\n')
  if (!text.trim()) return null
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0

  const renderItems = (items: ListItem[], ordered: boolean, depth: number): ReactNode => {
    const ListTag = ordered ? 'ol' : 'ul'
    return (
      <ListTag
        key={`l${key++}`}
        className={clsx(
          'space-y-1',
          ordered ? 'list-decimal' : items.every((item) => item.checked !== null) ? 'list-none' : 'list-disc',
          depth === 0 ? 'pl-5' : 'mt-1 pl-5',
        )}
      >
        {items.map((item) => (
          <li key={`li${item.line}`} className={clsx(item.checked !== null && '-ml-5 list-none')}>
            {item.checked !== null ? (
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 shrink-0 accent-[rgb(var(--accent))]"
                  checked={item.checked}
                  disabled={!onToggleTask}
                  onChange={(event) => onToggleTask?.(item.line, event.target.checked)}
                />
                <span className={clsx(item.checked && 'text-[rgb(var(--text-muted))] line-through')}>
                  {renderInline(item.text, `li${item.line}`)}
                </span>
              </label>
            ) : (
              renderInline(item.text, `li${item.line}`)
            )}
            {item.children.length > 0 && renderItems(item.children, ordered, depth + 1)}
          </li>
        ))}
      </ListTag>
    )
  }

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (trimmed === '') {
      i += 1
      continue
    }

    // fenced code
    if (trimmed.startsWith('```')) {
      const body: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        body.push(lines[i])
        i += 1
      }
      i += 1 // closing fence (or end of text)
      blocks.push(
        <pre
          key={`c${key++}`}
          className="overflow-x-auto rounded-xl bg-[rgb(var(--bg-sunken))] p-3 font-mono text-[13px] leading-relaxed"
        >
          <code>{body.join('\n')}</code>
        </pre>,
      )
      continue
    }

    // heading
    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed)
    if (heading) {
      const level = heading[1].length
      const content = renderInline(heading[2], `h${i}`)
      blocks.push(
        level === 1 ? (
          <h3 key={`h${key++}`} className="text-lg font-semibold">
            {content}
          </h3>
        ) : level === 2 ? (
          <h4 key={`h${key++}`} className="text-base font-semibold">
            {content}
          </h4>
        ) : (
          <h5 key={`h${key++}`} className="text-sm font-semibold uppercase tracking-wide text-[rgb(var(--text-muted))]">
            {content}
          </h5>
        ),
      )
      i += 1
      continue
    }

    // horizontal rule
    if (/^([-*_])(\s*\1){2,}$/.test(trimmed)) {
      blocks.push(<hr key={`r${key++}`} className="border-[rgb(var(--border))]" />)
      i += 1
      continue
    }

    // quote
    if (trimmed.startsWith('>')) {
      const quoted: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoted.push(lines[i].trim().replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push(
        <blockquote
          key={`q${key++}`}
          className="border-l-2 border-[rgb(var(--accent))]/60 pl-3 text-[rgb(var(--text-muted))]"
        >
          {quoted.map((part, index) => (
            <Fragment key={index}>
              {index > 0 && <br />}
              {renderInline(part, `q${i}.${index}`)}
            </Fragment>
          ))}
        </blockquote>,
      )
      continue
    }

    // list (one nesting level by indentation)
    const first = LIST_LINE.exec(line)
    if (first) {
      const block: ListBlock = { ordered: /\d/.test(first[2]), items: [] }
      const baseIndent = first[1].length
      while (i < lines.length) {
        const match = LIST_LINE.exec(lines[i])
        if (!match) {
          // continuation of the previous item ("lazy" line), until a blank line
          if (lines[i].trim() !== '' && block.items.length > 0 && /^\s{2,}/.test(lines[i])) {
            const items = block.items
            const last = items[items.length - 1]
            const target = last.children.length > 0 ? last.children[last.children.length - 1] : last
            target.text = `${target.text} ${lines[i].trim()}`
            i += 1
            continue
          }
          break
        }
        const item = parseListItem(match[3], i)
        if (match[1].length > baseIndent + 1 && block.items.length > 0) {
          block.items[block.items.length - 1].children.push(item)
        } else {
          block.items.push(item)
        }
        i += 1
      }
      blocks.push(renderItems(block.items, block.ordered, 0))
      continue
    }

    // paragraph: consecutive plain lines, single newlines kept as line breaks
    const paragraph: { text: string; line: number }[] = []
    while (i < lines.length) {
      const current = lines[i]
      const t = current.trim()
      if (t === '' || t.startsWith('```') || t.startsWith('>') || /^#{1,6}\s/.test(t) || LIST_LINE.test(current)) break
      if (/^([-*_])(\s*\1){2,}$/.test(t)) break
      paragraph.push({ text: t, line: i })
      i += 1
    }
    blocks.push(
      <p key={`p${key++}`}>
        {paragraph.map((part, index) => (
          <Fragment key={part.line}>
            {index > 0 && <br />}
            {renderInline(part.text, `p${part.line}`)}
          </Fragment>
        ))}
      </p>,
    )
  }

  return <div className={clsx('space-y-2 break-words text-sm leading-relaxed', className)}>{blocks}</div>
}

/** Flip the `[ ]`/`[x]` marker on a source line (for `onToggleTask`). */
export function toggleTaskLine(source: string, line: number, checked: boolean): string {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  if (line < 0 || line >= lines.length) return source
  lines[line] = lines[line].replace(/\[( |x|X)\]/, checked ? '[x]' : '[ ]')
  return lines.join('\n')
}
