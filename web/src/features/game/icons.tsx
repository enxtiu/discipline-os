/**
 * Gamification icons (stage 8) — the same grammar as `components/ui/icons.tsx`
 * (24×24, stroke `currentColor`, 1.75 width, round caps). The server names an
 * icon per quest and achievement (`icon: "egg"`, `"trophy"` …); `GameIcon`
 * maps those names and falls back to a star for anything unknown, so a new
 * seed row never renders a blank badge.
 */
import type { ReactNode, SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Base({ size = 24, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

const STAR = 'm12 4 2.5 5 5.5.8-4 3.9.9 5.4L12 16.6 7.1 19.1l1-5.4-4-3.9L9.5 9 12 4Z'
/** A flame with an inner tongue — reads as fire even at 16 px (a plain drop looked like water). */
const FLAME_OUTER = 'M12 2.5c.7 3.6 4.9 5.8 5.9 10 .2.8.3 1.5.3 2.2a6.2 6.2 0 0 1-12.4 0c0-2.2 1-4 2.3-5.4.2 1.5 1 2.6 2.1 3.1-.3-3.6.7-6.9 1.8-9.9Z'
const FLAME_INNER = 'M12 20.9a2.6 2.6 0 0 1-2.6-2.6c0-1.6 1.2-2.6 2.6-4.3 1.4 1.7 2.6 2.7 2.6 4.3a2.6 2.6 0 0 1-2.6 2.6Z'

/** name → the shapes inside the 24×24 box */
const SHAPES: Record<string, ReactNode> = {
  utensils: (
    <>
      <path d="M6.5 3v5a2.5 2.5 0 0 0 5 0V3" />
      <path d="M9 3v4.5M9 10.5V21" />
      <path d="M17.5 21V3c-1.9 1-3 3.2-3 6.2V13h3" />
    </>
  ),
  notebook: (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M9 3v18M12.5 8h3.5M12.5 12h3.5" />
    </>
  ),
  book: (
    <>
      <path d="M5 19.5v-15A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5Z" />
      <path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3" />
    </>
  ),
  library: (
    <>
      <path d="M5 4h3v16H5zM10 4h3v16h-3z" />
      <path d="m15.2 5.4 2.9-.8 3.4 14.5-2.9.8z" />
    </>
  ),
  egg: <path d="M12 3c-3.6 0-6.5 5.2-6.5 9.5a6.5 6.5 0 0 0 13 0C18.5 8.2 15.6 3 12 3Z" />,
  dumbbell: (
    <>
      <path d="M6.5 6.5v11M17.5 6.5v11" />
      <path d="M3.5 9.5v5M20.5 9.5v5" />
      <path d="M6.5 12h11" />
    </>
  ),
  droplet: <path d="M12 3.5S6.5 9.2 6.5 13a5.5 5.5 0 0 0 11 0c0-3.8-5.5-9.5-5.5-9.5Z" />,
  waves: (
    <path d="M3 7.5c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1 1.5 1 3 1M3 12.5c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1 1.5 1 3 1M3 17.5c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1 1.5 1 3 1" />
  ),
  activity: <path d="M3 12h4l2.5-6 4 12 2.5-6H21" />,
  medal: (
    <>
      <path d="M8 3h8M8.5 3l2.2 6.3M15.5 3l-2.2 6.3" />
      <circle cx="12" cy="15" r="5.5" />
      <path d="m12 12.4.9 1.8 2 .3-1.5 1.4.4 2-1.8-1-1.8 1 .4-2-1.5-1.4 2-.3.9-1.8Z" />
    </>
  ),
  trophy: (
    <>
      <path d="M8 4h8v5a4 4 0 0 1-8 0V4Z" />
      <path d="M8 6H5.5a2.5 2.5 0 0 0 2.6 3.5M16 6h2.5a2.5 2.5 0 0 1-2.6 3.5" />
      <path d="M12 13v4M8.5 20h7M10 17h4" />
    </>
  ),
  footprints: (
    <>
      <path d="M7 4.5c1.7 0 2.5 1.4 2.5 3.5S9.8 12 8 12s-3-1-3-4 1-3.5 2-3.5Z" />
      <path d="M9.5 14.5c0 2-.5 5-2.5 5s-2.5-2-2.5-3.5.8-2 2.5-2 2.5.6 2.5.5Z" />
      <path d="M17 6.5c1 0 2 .5 2 3.5s-1.2 4-3 4-2.5-1.9-2.5-4S15.3 6.5 17 6.5Z" />
      <path d="M14.5 16.5c0 1.5-.5 3.5-2.5 3.5" />
    </>
  ),
  map: (
    <>
      <path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6 9 4Z" />
      <path d="M9 4v14M15 6v14" />
    </>
  ),
  mountain: (
    <>
      <path d="M3 20 9.5 8l4 7 2.5-4L21 20H3Z" />
      <path d="m8 10.8 1.5 1.2 1.6-1.1" />
    </>
  ),
  moon: <path d="M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10Z" />,
  scale: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="4" />
      <path d="M8.8 10a4 4 0 0 1 6.4 0M12 10.2l1.3-2" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  'check-check': (
    <>
      <path d="m2.5 12.5 4 4 9-9.5" />
      <path d="m12.5 15.5 1 1 8.5-9" />
    </>
  ),
  'list-checks': (
    <>
      <path d="m3.5 6.5 1.5 1.5 3-3M3.5 13.5 5 15l3-3" />
      <path d="M11.5 7h9M11.5 14h9M11.5 19.5h9" />
    </>
  ),
  flag: (
    <>
      <path d="M5.5 21V4" />
      <path d="M5.5 4.5h12l-2.5 4 2.5 4h-12" />
    </>
  ),
  'folder-check': (
    <>
      <path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5v-10Z" />
      <path d="m9 13.5 2 2 4-4" />
    </>
  ),
  rocket: (
    <>
      <path d="M13.5 15.5 8.5 10.5C10.5 5.5 14.5 3 20.5 3.5c.5 6-2 10-7 12Z" />
      <circle cx="15.5" cy="8.5" r="1.5" />
      <path d="m8.5 10.5-3.5-.5 2.5-3.5h4M13.5 15.5l.5 3.5 3.5-2.5v-4" />
      <path d="M6.5 15c-1.6 1-2.2 3.5-2.2 4.7 1.2 0 3.7-.6 4.7-2.2" />
    </>
  ),
  timer: (
    <>
      <circle cx="12" cy="13.5" r="7" />
      <path d="M12 13.5v-3.5M10 3.5h4M18 6.5l1.5-1.5" />
    </>
  ),
  hourglass: (
    <>
      <path d="M6.5 3.5h11M6.5 20.5h11" />
      <path d="M8 3.5v3a4 4 0 0 0 1.6 3.2L12 12l2.4-2.3A4 4 0 0 0 16 6.5v-3M8 20.5v-3a4 4 0 0 1 1.6-3.2L12 12l2.4 2.3a4 4 0 0 1 1.6 3.2v3" />
    </>
  ),
  pen: <path d="m15.5 4.5 4 4L9 19H5v-4L15.5 4.5Z" />,
  'pen-line': (
    <>
      <path d="m14.5 4 3.5 3.5-9 9H5.5V13l9-9Z" />
      <path d="M13 20.5h7" />
    </>
  ),
  'book-open': (
    <>
      <path d="M12 6.5C10 5 7.5 4.5 3.5 4.5V18c4 0 6.5.5 8.5 2 2-1.5 4.5-2 8.5-2V4.5c-4 0-6.5.5-8.5 2Z" />
      <path d="M12 6.5V20" />
    </>
  ),
  scroll: (
    <>
      <path d="M7.5 17.5V5.5a2 2 0 0 1 2-2h9" />
      <path d="M18.5 3.5a2 2 0 0 1 0 4H16.5v11a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-1h11" />
      <path d="M10.5 8h3M10.5 11.5h3" />
    </>
  ),
  flame: (
    <>
      <path d={FLAME_OUTER} />
      <path d={FLAME_INNER} />
    </>
  ),
  crown: (
    <>
      <path d="m4 17.5-1-10 5 4 4-6.5 4 6.5 5-4-1 10H4Z" />
      <path d="M4.5 20.5h15" />
    </>
  ),
  'calendar-check': (
    <>
      <rect x="4" y="5.5" width="16" height="14.5" rx="2.5" />
      <path d="M8 3.5v4M16 3.5v4M4 10h16" />
      <path d="m9 15 2 2 4-4" />
    </>
  ),
  'calendar-range': (
    <>
      <rect x="4" y="5.5" width="16" height="14.5" rx="2.5" />
      <path d="M8 3.5v4M16 3.5v4M4 10h16" />
      <path d="M7.5 14h4M12.5 17h4" />
    </>
  ),
  star: <path d={STAR} />,
  stars: (
    <>
      <path d="m10 6 2 4 4.4.6-3.2 3.1.8 4.3L10 16l-4 2 .8-4.3-3.2-3.1L8 10l2-4Z" />
      <path d="M18.5 3v4M16.5 5h4" />
    </>
  ),
  gem: (
    <>
      <path d="M7 4h10l4 5-9 11L3 9l4-5Z" />
      <path d="M3 9h18M12 20 9 9l2-5M12 20l3-11-2-5" />
    </>
  ),
  sparkles: (
    <>
      <path d="m11 4 1.8 5.2L18 11l-5.2 1.8L11 18l-1.8-5.2L4 11l5.2-1.8L11 4Z" />
      <path d="M19 15v5M16.5 17.5h5M5 2.5v3M3.5 4h3" />
    </>
  ),
  zap: <path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12l1-8Z" />,
  'shield-check': (
    <>
      <path d="M12 3 4.5 6v5.5c0 4.5 3.2 8 7.5 9.5 4.3-1.5 7.5-5 7.5-9.5V6L12 3Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  compass: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m15.5 8.5-2 5-5 2 2-5 5-2Z" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="2" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5M12 14.5v2" />
    </>
  ),
}

/** A server-named quest/achievement icon (unknown names → star). */
export function GameIcon({ icon, ...props }: IconProps & { icon: string | null | undefined }) {
  return <Base {...props}>{(icon && SHAPES[icon]) || SHAPES.star}</Base>
}

/** Streak flame; `lit` fills it (today already counts). */
export function IconFlameLit({ lit, ...props }: IconProps & { lit?: boolean }) {
  return (
    <Base {...props}>
      <path d={FLAME_OUTER} fill={lit ? 'currentColor' : 'none'} fillOpacity={lit ? 0.3 : 0} />
      <path d={FLAME_INNER} fill={lit ? 'currentColor' : 'none'} fillOpacity={lit ? 0.6 : 0} />
    </Base>
  )
}

export function IconTrophy(props: IconProps) {
  return <GameIcon icon="trophy" {...props} />
}
