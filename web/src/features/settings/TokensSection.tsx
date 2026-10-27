/**
 * Scoped API tokens for iOS Shortcuts and scripts.
 * The secret is displayed exactly once (with a QR code for easy transfer to a
 * Shortcut) and every token shows its scopes in both languages.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QRCodeSVG } from 'qrcode.react'

import {
  Badge,
  Button,
  Callout,
  Field,
  Input,
  Rows,
  Row,
  Sheet,
  SkeletonCard,
} from '@/components/ui'
import { IconCopy, IconKey, IconTrash } from '@/components/ui/icons'
import { endpoints } from '@/api/client'
import type { ApiToken } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { relativeTime } from '@/lib/format'

export function TokensSection() {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()

  const tokensQuery = useQuery({ queryKey: queryKeys.tokens, queryFn: () => endpoints.tokens() })
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('iOS Shortcut')
  const [selected, setSelected] = useState<string[]>(['ingest:health'])
  const [issued, setIssued] = useState<{ token: string; apiToken: ApiToken } | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.tokens })

  const createMutation = useMutation({
    mutationFn: () => endpoints.createToken({ name, scopes: selected }),
    onSuccess: async (result) => {
      setIssued({ token: result.token, apiToken: result.api_token })
      setCreating(false)
      await invalidate()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const revokeMutation = useMutation({
    mutationFn: (id: string) => endpoints.revokeToken(id),
    onSuccess: async () => {
      await invalidate()
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  if (tokensQuery.isLoading) return <SkeletonCard lines={4} />
  if (tokensQuery.isError || !tokensQuery.data) return null

  const { tokens, scopes } = tokensQuery.data
  const language = (i18n.resolvedLanguage ?? 'ru') as 'ru' | 'en'

  return (
    <div className="space-y-4 pb-6">
      <h1 className="h1">{t('settings.tokens_title')}</h1>
      <p className="text-sm muted">{t('settings.tokens_hint')}</p>

      <Button block variant="secondary" icon={<IconKey size={18} />} onClick={() => setCreating(true)}>
        {t('settings.token_create')}
      </Button>

      {tokens.length === 0 ? (
        <Callout tone="info">—</Callout>
      ) : (
        <Rows>
          {tokens.map((token) => (
            <Row
              key={token.id}
              icon={<IconKey size={18} />}
              label={
                <span className="flex items-center gap-2">
                  {token.name}
                  {!token.is_active && <Badge tone="danger">{t('errors.token_revoked')}</Badge>}
                </span>
              }
              hint={
                <span className="flex flex-wrap gap-1.5">
                  {token.scopes.map((scope) => (
                    <span key={scope} className="chip py-0.5 text-[11px]">
                      {scope}
                    </span>
                  ))}
                  <span className="w-full">
                    {token.last_used_at
                      ? `${t('settings.token_used_count', { count: token.request_count })} · ${relativeTime(token.last_used_at)}`
                      : t('settings.token_never_used')}
                  </span>
                </span>
              }
              trailing={
                token.is_active && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="w-tap px-0 text-rose-400"
                    aria-label={t('a11y.token_revoke', { name: token.name })}
                    title={t('settings.token_revoke')}
                    onClick={() => revokeMutation.mutate(token.id)}
                  >
                    <IconTrash size={16} />
                  </Button>
                )
              }
            />
          ))}
        </Rows>
      )}

      <Sheet open={creating} onClose={() => setCreating(false)} title={t('settings.token_create')}>
        <div className="space-y-4 pb-2">
          <Field label={t('settings.token_name')}>
            <Input value={name} onChange={(event) => setName(event.target.value)} maxLength={80} />
          </Field>
          <Field label={t('settings.token_scopes')}>
            <div className="card divide-y divide-[rgb(var(--border))]">
              {scopes.map((scope) => (
                <label key={scope.scope} className="flex items-center gap-3 px-4 py-3">
                  <input
                    type="checkbox"
                    className="h-5 w-5 accent-aqua-400"
                    checked={selected.includes(scope.scope)}
                    onChange={(event) =>
                      setSelected((current) =>
                        event.target.checked
                          ? [...current, scope.scope]
                          : current.filter((value) => value !== scope.scope),
                      )
                    }
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block font-stat text-xs">{scope.scope}</span>
                    <span className="block text-xs muted">
                      {language === 'en' ? scope.description_en : scope.description_ru}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </Field>
          <Button
            block
            disabled={selected.length === 0 || !name.trim()}
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            {t('settings.token_create')}
          </Button>
        </div>
      </Sheet>

      <Sheet open={!!issued} onClose={() => setIssued(null)} title={t('settings.tokens_title')}>
        {issued && (
          <div className="space-y-4 pb-2">
            <Callout tone="warn" title={t('settings.token_created')}>
              <span className="block break-all font-mono text-xs">{issued.token}</span>
            </Callout>
            <div className="flex justify-center">
              <div className="rounded-2xl bg-white p-3">
                <QRCodeSVG value={issued.token} size={168} />
              </div>
            </div>
            <Button
              block
              icon={<IconCopy size={16} />}
              onClick={() => {
                void navigator.clipboard.writeText(issued.token)
                toast.success(t('app.copied'))
              }}
            >
              {t('app.copy')}
            </Button>
            <p className="text-xs muted">
              POST {window.location.origin}/api/v1/ingest/health — Authorization: Bearer &lt;token&gt;
            </p>
          </div>
        )}
      </Sheet>
    </div>
  )
}
