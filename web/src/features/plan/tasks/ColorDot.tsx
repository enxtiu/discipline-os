/** A project/area/tag colour dot; muted when the container has no colour. Tiny on purpose (main chunk). */
import clsx from 'clsx'

export function ColorDot({ color, className }: { color: string | null | undefined; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={clsx('inline-block h-2 w-2 shrink-0 rounded-full', !color && 'bg-[rgb(var(--text-muted))]/60', className)}
      style={color ? { backgroundColor: color } : undefined}
    />
  )
}
