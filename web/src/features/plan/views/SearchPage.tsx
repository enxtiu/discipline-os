/**
 * /plan/search — one field (Russian and English full-text search on the
 * server), results grouped as tasks / projects / areas / resources / journal,
 * an include-done switch, and the tag list while the field is empty.
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { EmptyState, ErrorState, Input, Rows, Toggle } from '@/components/ui'
import { IconChevronRight, IconSearch } from '@/components/ui/icons'
import { dayLabel } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { useCounts, useSearch, useTags } from '../queries'
import type { Resource } from '../types'
import { ProjectRow } from '../para/ProjectsPage'
import { ResourceSheet } from '../para/ResourceSheet'
import { ResourceRow } from '../para/ResourcesPage'
import { TaskGroup, TaskListSkeleton } from '../tasks/TaskList'
import { TaskRow } from '../tasks/TaskRow'
import { ColorDot } from '../tasks/ui'

function SectionTitle({ children, count }: { children: string; count: number }) {
  return (
    <h2 className="flex items-center gap-2 px-1 text-sm font-semibold">
      {children}
      <span className="tnum rounded-pill bg-[rgb(var(--border))]/60 px-1.5 text-[11px] font-medium text-[rgb(var(--text-muted))]">
        {count}
      </span>
    </h2>
  )
}

export function SearchPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [input, setInput] = useState(() => params.get('q') ?? '')
  const [query, setQuery] = useState(input)
  const [includeDone, setIncludeDone] = useState(false)
  const [resource, setResource] = useState<Resource | null | undefined>(undefined)
  const today = useCounts().data?.date
  const tags = useTags()
  const search = useSearch(query, includeDone)
  const active = query.trim().length >= 2

  // debounce; keep the query in the URL so "back" returns to the same results
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setQuery(input)
      setParams(input.trim() ? { q: input } : {}, { replace: true })
    }, 250)
    return () => window.clearTimeout(handle)
  }, [input, setParams])

  const data = active ? search.data : undefined
  const total = data
    ? data.tasks.length + data.projects.length + data.areas.length + data.resources.length + data.journal.length
    : 0

  return (
    <div className="space-y-4" data-testid="search-page">
      <div className="relative">
        <IconSearch size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 muted" />
        <Input
          type="search"
          autoFocus
          enterKeyHint="search"
          className="pl-11"
          maxLength={100}
          value={input}
          placeholder={t('plan.search_placeholder')}
          aria-label={t('plan.search')}
          onChange={(event) => setInput(event.target.value)}
          data-testid="search-input"
        />
      </div>
      <label className="flex min-h-tap cursor-pointer items-center justify-between gap-3 px-1">
        <span className="text-sm">{t('plan.include_done')}</span>
        <Toggle checked={includeDone} onChange={setIncludeDone} label={t('plan.include_done')} />
      </label>

      {!active && (
        <div className="space-y-3">
          <p className="px-1 text-sm muted">{t('plan.search_hint')}</p>
          {(tags.data?.items.length ?? 0) > 0 && (
            <section className="space-y-1.5">
              <h2 className="px-1 text-sm font-semibold">{t('plan.tags')}</h2>
              <div className="flex flex-wrap gap-2" data-testid="tag-list">
                {tags.data?.items.map((tag) => (
                  <Link key={tag.id} to={`/plan/tags/${tag.id}`} className="chip min-h-tap" data-testid="tag-link">
                    <ColorDot color={tag.color} />#{tag.name}
                    {tag.open_tasks > 0 && <span className="tnum text-xs">{tag.open_tasks}</span>}
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {active && search.isLoading && <TaskListSkeleton rows={3} />}
      {active && search.isError && (
        <ErrorState message={errorMessage(search.error)} onRetry={() => void search.refetch()} retryLabel={t('app.retry')} />
      )}
      {data && total === 0 && (
        <EmptyState icon={<IconSearch size={28} />} title={t('plan.search_empty', { query: data.query })} />
      )}

      {data && data.tasks.length > 0 && (
        <TaskGroup title={t('plan.search_tasks')} count={data.tasks.length} testId="search-tasks">
          {data.tasks.map((task) => (
            <TaskRow key={task.id} task={task} today={today} />
          ))}
        </TaskGroup>
      )}
      {data && data.projects.length > 0 && (
        <section className="space-y-1.5" data-testid="search-projects">
          <SectionTitle count={data.projects.length}>{t('plan.projects')}</SectionTitle>
          <Rows>
            {data.projects.map((project) => (
              <ProjectRow key={project.id} project={project} today={today} />
            ))}
          </Rows>
        </section>
      )}
      {data && data.areas.length > 0 && (
        <section className="space-y-1.5" data-testid="search-areas">
          <SectionTitle count={data.areas.length}>{t('plan.areas')}</SectionTitle>
          <Rows>
            {data.areas.map((area) => (
              <button
                key={area.id}
                type="button"
                onClick={() => navigate(`/plan/areas/${area.id}`)}
                className="flex min-h-tap w-full items-center gap-2 px-4 py-3 text-left"
              >
                <ColorDot color={area.color} className="h-2.5 w-2.5" />
                <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{area.name}</span>
                <IconChevronRight size={16} className="muted" />
              </button>
            ))}
          </Rows>
        </section>
      )}
      {data && data.resources.length > 0 && (
        <section className="space-y-1.5" data-testid="search-resources">
          <SectionTitle count={data.resources.length}>{t('plan.resources')}</SectionTitle>
          <Rows>
            {data.resources.map((item) => (
              <ResourceRow key={item.id} resource={item} onOpen={setResource} />
            ))}
          </Rows>
        </section>
      )}
      {data && data.journal.length > 0 && (
        <section className="space-y-1.5" data-testid="search-journal">
          <SectionTitle count={data.journal.length}>{t('plan.journal')}</SectionTitle>
          <Rows>
            {data.journal.map((hit) => (
              <Link
                key={hit.date}
                to={`/plan/journal?date=${hit.date}`}
                className="block px-4 py-3 transition-colors active:bg-[rgb(var(--border))]/30"
              >
                <span className="block text-sm font-medium first-letter:uppercase">{dayLabel(hit.date, 'long')}</span>
                <span className="mt-0.5 line-clamp-2 block text-xs muted">{hit.snippet}</span>
              </Link>
            ))}
          </Rows>
        </section>
      )}

      <ResourceSheet open={resource !== undefined} resource={resource ?? null} onClose={() => setResource(undefined)} />
    </div>
  )
}
