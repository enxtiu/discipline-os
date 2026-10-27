/**
 * Planner icons (stage 6) — same grammar as `components/ui/icons.tsx`
 * (24×24, stroke `currentColor`, 1.75 width, round caps), kept here so the
 * planner workstreams can add icons without touching the shared set.
 */
import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

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

export const IconPlay = (props: IconProps) => (
  <Base {...props}>
    <path d="M8 5.5v13l10.5-6.5L8 5.5Z" />
  </Base>
)

export const IconStop = (props: IconProps) => (
  <Base {...props}>
    <rect x="6.5" y="6.5" width="11" height="11" rx="2" />
  </Base>
)

export const IconInbox = (props: IconProps) => (
  <Base {...props}>
    <path d="M4 13.5 6.5 5h11L20 13.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-5.5Z" />
    <path d="M4 13.5h4.5l1.5 2.5h4l1.5-2.5H20" />
  </Base>
)

export const IconCalendar = (props: IconProps) => (
  <Base {...props}>
    <rect x="4" y="5.5" width="16" height="14.5" rx="2.5" />
    <path d="M8 3.5v4M16 3.5v4M4 10h16" />
  </Base>
)

export const IconRepeat = (props: IconProps) => (
  <Base {...props}>
    <path d="M17 3.5 20 6.5l-3 3" />
    <path d="M4 11.5V10a3.5 3.5 0 0 1 3.5-3.5H20" />
    <path d="M7 20.5 4 17.5l3-3" />
    <path d="M20 12.5V14a3.5 3.5 0 0 1-3.5 3.5H4" />
  </Base>
)

export const IconFlag = (props: IconProps) => (
  <Base {...props}>
    <path d="M5.5 21V4.5" />
    <path d="M5.5 4.5h11l-2 4 2 4h-11" />
  </Base>
)

export const IconTag = (props: IconProps) => (
  <Base {...props}>
    <path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.5 1.5 0 0 1 0 2.1l-6.3 6.3a1.5 1.5 0 0 1-2.1 0L3.5 12.2Z" />
    <circle cx="8" cy="8" r="1.4" />
  </Base>
)

export const IconFolder = (props: IconProps) => (
  <Base {...props}>
    <path d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4.3l2 2.5H19a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5V7Z" />
  </Base>
)

export const IconLayers = (props: IconProps) => (
  <Base {...props}>
    <path d="m12 3.5 8.5 4.5-8.5 4.5L3.5 8 12 3.5Z" />
    <path d="m3.5 12 8.5 4.5 8.5-4.5" />
    <path d="m3.5 16 8.5 4.5 8.5-4.5" />
  </Base>
)

export const IconBook = (props: IconProps) => (
  <Base {...props}>
    <path d="M5 4.5h9.5A3.5 3.5 0 0 1 18 8v11.5H8.5A3.5 3.5 0 0 1 5 16V4.5Z" />
    <path d="M5 16a3.5 3.5 0 0 1 3.5-3.5H18" />
  </Base>
)

export const IconArchive = (props: IconProps) => (
  <Base {...props}>
    <rect x="3.5" y="4.5" width="17" height="4.5" rx="1" />
    <path d="M5 9v9.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9" />
    <path d="M10 13h4" />
  </Base>
)

export const IconNote = (props: IconProps) => (
  <Base {...props}>
    <path d="M6 3.5h8.5L19 8v12.5H6V3.5Z" />
    <path d="M14 3.5V8h5M9 12.5h6M9 16h6" />
  </Base>
)

export const IconGrip = (props: IconProps) => (
  <Base {...props}>
    <circle cx="9" cy="7" r="0.9" fill="currentColor" />
    <circle cx="15" cy="7" r="0.9" fill="currentColor" />
    <circle cx="9" cy="12" r="0.9" fill="currentColor" />
    <circle cx="15" cy="12" r="0.9" fill="currentColor" />
    <circle cx="9" cy="17" r="0.9" fill="currentColor" />
    <circle cx="15" cy="17" r="0.9" fill="currentColor" />
  </Base>
)

export const IconMore = (props: IconProps) => (
  <Base {...props}>
    <circle cx="6" cy="12" r="1.2" fill="currentColor" />
    <circle cx="12" cy="12" r="1.2" fill="currentColor" />
    <circle cx="18" cy="12" r="1.2" fill="currentColor" />
  </Base>
)

export const IconSubtasks = (props: IconProps) => (
  <Base {...props}>
    <path d="M5 4.5v11a2 2 0 0 0 2 2h4" />
    <path d="M5 9.5h6" />
    <rect x="13" y="7.5" width="7" height="4" rx="1" />
    <rect x="13" y="15.5" width="7" height="4" rx="1" />
  </Base>
)

export const IconTimer = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="13.5" r="7" />
    <path d="M12 13.5V10M9.5 3.5h5M12 3.5v3" />
  </Base>
)

export const IconBellSmall = (props: IconProps) => (
  <Base {...props}>
    <path d="M7 16.5V11a5 5 0 0 1 10 0v5.5l1.5 1.5h-13L7 16.5Z" />
    <path d="M10.5 20.5a1.5 1.5 0 0 0 3 0" />
  </Base>
)

export const IconLink = (props: IconProps) => (
  <Base {...props}>
    <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
    <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
  </Base>
)

export const IconArrowRight = (props: IconProps) => (
  <Base {...props}>
    <path d="M4.5 12h15M14 6.5l5.5 5.5-5.5 5.5" />
  </Base>
)
