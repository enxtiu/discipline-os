/**
 * Inline SVG icon set (no icon-font, no network requests).
 * All icons are 24x24, stroke-based, `currentColor`, 1.75 stroke width so they
 * stay legible at the 20–24 px sizes used in the tab bar and cards.
 */
import type { SVGProps } from 'react'

export type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Base({ size = 24, children, ...rest }: IconProps) {
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

export const IconToday = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Base>
)

export const IconFood = (props: IconProps) => (
  <Base {...props}>
    <path d="M6 3v8a3 3 0 0 0 3 3v7" />
    <path d="M6 3v6M9 3v6" />
    <path d="M16 3c-1.5 2-2 3.5-2 6 0 1.7.7 2.7 2 2.7S18 10.7 18 9c0-2.5-.5-4-2-6Z" />
    <path d="M16 11.7V21" />
  </Base>
)

export const IconTrain = (props: IconProps) => (
  <Base {...props}>
    <path d="M6.5 6.5v11M17.5 6.5v11" />
    <path d="M3.5 9.5v5M20.5 9.5v5" />
    <path d="M6.5 12h11" />
  </Base>
)

export const IconPlan = (props: IconProps) => (
  <Base {...props}>
    <rect x="3.5" y="5" width="17" height="15" rx="3" />
    <path d="M8 3.5v3M16 3.5v3M3.5 10h17" />
    <path d="M8 14h3v3H8z" />
  </Base>
)

export const IconStats = (props: IconProps) => (
  <Base {...props}>
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
  </Base>
)

export const IconPlus = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 5v14M5 12h14" />
  </Base>
)

export const IconSettings = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 2.6 14H2.4a2 2 0 1 1 0-4h.2A1.7 1.7 0 0 0 4.6 7a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 2.6V2.4a2 2 0 1 1 4 0v.2a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21.4 10h.2a2 2 0 1 1 0 4h-.2a1.7 1.7 0 0 0-1.2 1Z" />
  </Base>
)

export const IconClose = (props: IconProps) => (
  <Base {...props}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Base>
)

export const IconChevronRight = (props: IconProps) => (
  <Base {...props}>
    <path d="M9 6l6 6-6 6" />
  </Base>
)

export const IconChevronLeft = (props: IconProps) => (
  <Base {...props}>
    <path d="M15 6l-6 6 6 6" />
  </Base>
)

export const IconCheck = (props: IconProps) => (
  <Base {...props}>
    <path d="M4.5 12.5 9 17l10.5-10.5" />
  </Base>
)

export const IconTrash = (props: IconProps) => (
  <Base {...props}>
    <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
  </Base>
)

export const IconCopy = (props: IconProps) => (
  <Base {...props}>
    <rect x="9" y="9" width="11" height="11" rx="2.5" />
    <path d="M15 5.5A2.5 2.5 0 0 0 12.5 3H6.5A2.5 2.5 0 0 0 4 5.5v6A2.5 2.5 0 0 0 6.5 14" />
  </Base>
)

export const IconPhone = (props: IconProps) => (
  <Base {...props}>
    <rect x="6" y="2.5" width="12" height="19" rx="3" />
    <path d="M11 18.5h2" />
  </Base>
)

export const IconLaptop = (props: IconProps) => (
  <Base {...props}>
    <rect x="4" y="5" width="16" height="11" rx="2" />
    <path d="M2 19h20" />
  </Base>
)

export const IconKey = (props: IconProps) => (
  <Base {...props}>
    <circle cx="8" cy="15" r="4" />
    <path d="M11 12 20 3M17 4.5 19.5 7M14.5 7 17 9.5" />
  </Base>
)

export const IconShield = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 3 5 6v5.5c0 4.3 2.9 7.7 7 9.5 4.1-1.8 7-5.2 7-9.5V6l-7-3Z" />
    <path d="M9.5 12.2 11.5 14l3.2-3.6" />
  </Base>
)

export const IconBell = (props: IconProps) => (
  <Base {...props}>
    <path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" />
    <path d="M10 20a2 2 0 0 0 4 0" />
  </Base>
)

export const IconGlobe = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.2 2.4 3.3 5.3 3.3 8.5S14.2 18.1 12 20.5c-2.2-2.4-3.3-5.3-3.3-8.5S9.8 5.9 12 3.5Z" />
  </Base>
)

export const IconPalette = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 2-.9 2-1.9 0-1.4-1.3-1.7-1.3-2.9 0-.8.7-1.4 1.6-1.4h1.6a4.6 4.6 0 0 0 4.6-4.6c0-3.6-3.3-6.2-8.5-6.2Z" />
    <circle cx="8" cy="10" r="1" fill="currentColor" />
    <circle cx="12" cy="7.8" r="1" fill="currentColor" />
    <circle cx="16" cy="10" r="1" fill="currentColor" />
  </Base>
)

export const IconUser = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="8.5" r="3.5" />
    <path d="M5 20c0-3.3 3.1-5.5 7-5.5s7 2.2 7 5.5" />
  </Base>
)

export const IconFlame = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 3s4.5 4 4.5 8.5A4.5 4.5 0 0 1 12 16a4.5 4.5 0 0 1-4.5-4.5C7.5 7 12 3 12 3Z" />
    <path d="M9.5 20h5" />
  </Base>
)

export const IconDroplet = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 3.5S6.5 9.2 6.5 13a5.5 5.5 0 0 0 11 0c0-3.8-5.5-9.5-5.5-9.5Z" />
  </Base>
)

export const IconFootprints = (props: IconProps) => (
  <Base {...props}>
    <path d="M7 4.5c1.7 0 2.5 1.4 2.5 3.5S9.8 12 8 12s-3-1-3-4 1-3.5 2-3.5Z" />
    <path d="M9.5 14.5c0 2-.5 5-2.5 5s-2.5-2-2.5-3.5.8-2 2.5-2 2.5.6 2.5.5Z" />
    <path d="M17 6.5c1 0 2 .5 2 3.5s-1.2 4-3 4-2.5-1.9-2.5-4S15.3 6.5 17 6.5Z" />
    <path d="M14.5 16.5c0 1.5-.5 3.5-2.5 3.5" />
  </Base>
)

export const IconWifiOff = (props: IconProps) => (
  <Base {...props}>
    <path d="M3 4l18 16" />
    <path d="M8.5 12.5a5 5 0 0 1 4-1.4M5 9a10 10 0 0 1 4-2.4M15.5 9.4A10 10 0 0 1 19 12" />
    <path d="M10.5 16.2a2.5 2.5 0 0 1 3.2.2" />
    <circle cx="12" cy="19.5" r=".5" fill="currentColor" />
  </Base>
)

export const IconCloudCheck = (props: IconProps) => (
  <Base {...props}>
    <path d="M7 18.5h9.5a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.5-1.4A3.6 3.6 0 0 0 7 18.5Z" />
    <path d="M9.5 14.5l2 2 3.5-4" />
  </Base>
)

export const IconRefresh = (props: IconProps) => (
  <Base {...props}>
    <path d="M20 12a8 8 0 1 1-2.3-5.7" />
    <path d="M20 4v4h-4" />
  </Base>
)

export const IconInfo = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 8.2v.1" />
  </Base>
)

export const IconAlert = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 4.5 3.5 19h17L12 4.5Z" />
    <path d="M12 10v4M12 16.6v.1" />
  </Base>
)

export const IconScan = (props: IconProps) => (
  <Base {...props}>
    <path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" />
    <path d="M4 12h16" />
  </Base>
)

export const IconCamera = (props: IconProps) => (
  <Base {...props}>
    <path d="M3.5 8.5A2 2 0 0 1 5.5 6.5h1.4l1-1.8h6.2l1 1.8h1.4a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-8Z" />
    <circle cx="12" cy="12.5" r="3.2" />
  </Base>
)

export const IconText = (props: IconProps) => (
  <Base {...props}>
    <path d="M5 6.5h14M12 6.5V18M9 18h6" />
  </Base>
)

export const IconSearch = (props: IconProps) => (
  <Base {...props}>
    <circle cx="11" cy="11" r="6" />
    <path d="M15.5 15.5 20 20" />
  </Base>
)

export const IconStar = (props: IconProps) => (
  <Base {...props}>
    <path d="m12 4 2.5 5 5.5.8-4 3.9.9 5.4L12 16.6 7.1 19.1l1-5.4-4-3.9L9.5 9 12 4Z" />
  </Base>
)

export const IconClock = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3.5 2" />
  </Base>
)

/** Pushpin; `filled` marks the pinned state (also exposed via aria-pressed). */
export const IconPin = ({ filled, ...props }: IconProps & { filled?: boolean }) => (
  <Base {...props}>
    <path
      d="M9 4h6l-1 5 3.5 3.5V14h-11v-1.5L10 9 9 4Z"
      fill={filled ? 'currentColor' : 'none'}
    />
    <path d="M12 14v6" />
  </Base>
)
