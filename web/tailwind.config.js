/** A palette shade that follows the theme: the value lives in `src/styles/index.css`. */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // <Button variant> builds `btn-${variant}` at runtime, which the content
  // scanner cannot see — without this the variants are purged from the build.
  safelist: ['btn-primary', 'btn-secondary', 'btn-ghost', 'btn-danger'],
  theme: {
    extend: {
      colors: {
        // Near-black blue-gray base (never pure black) + soft contrast text.
        ink: {
          950: '#080D16',
          900: '#0B1220',
          800: '#111A2B',
          700: '#182236',
          600: '#1F2C45',
          500: '#2A3A57',
          400: '#3B4E70',
        },
        mist: {
          100: '#E8EEF7',
          200: '#CBD5E5',
          300: '#A7B4CA',
          400: '#8492AB',
          500: '#65728C',
        },
        // Restrained neon accents. The shades used for text are theme tokens (dark = the
        // original hex, light = a darker shade of the same hue with 4.5:1 contrast).
        aqua: {
          300: token('aqua-300'),
          400: token('aqua-400'),
          500: '#16C7AE',
          600: '#0EA393',
        },
        violet: {
          300: token('violet-300'),
          400: token('violet-400'),
          500: '#8B5CF6',
        },
        amber: {
          100: token('amber-100'),
          200: token('amber-200'),
          300: token('amber-300'),
          400: token('amber-400'),
          500: '#F59E0B',
        },
        rose: {
          200: token('rose-200'),
          300: token('rose-300'),
          400: token('rose-400'),
          500: '#F43F5E',
        },
        // text on the accent fills (dark ink in the dark theme, white in the light one)
        'on-accent': token('on-accent'),
        'on-accent-2': token('on-accent-2'),
      },
      fontFamily: {
        sans: [
          'InterVariable',
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'SF Pro Text',
          'Segoe UI',
          'Roboto',
          'system-ui',
          'sans-serif',
        ],
        // stats & numbers: tabular figures so columns line up
        stat: [
          'InterVariable',
          'Inter',
          'SF Mono',
          'ui-monospace',
          'monospace',
        ],
      },
      fontFeatureSettings: {
        tabular: '"tnum" 1, "cv05" 1',
      },
      borderRadius: {
        card: '1.25rem',
        pill: '999px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,.24), 0 8px 24px -12px rgba(0,0,0,.5)',
        glow: '0 0 0 1px rgba(56,224,200,.35), 0 0 24px -4px rgba(56,224,200,.45)',
        'glow-violet': '0 0 0 1px rgba(167,139,250,.35), 0 0 24px -4px rgba(167,139,250,.45)',
        'glow-amber': '0 0 0 1px rgba(251,191,36,.35), 0 0 22px -6px rgba(251,191,36,.4)',
      },
      spacing: {
        tap: '3rem', // 48 px minimum touch target
        'safe-b': 'env(safe-area-inset-bottom, 0px)',
      },
      keyframes: {
        'pulse-soft': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '.55' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        'ring-in': {
          from: { strokeDashoffset: 'var(--ring-circumference)' },
          to: { strokeDashoffset: 'var(--ring-offset)' },
        },
      },
      animation: {
        'pulse-soft': 'pulse-soft 2.4s ease-in-out infinite',
        shimmer: 'shimmer 1.6s infinite',
        'ring-in': 'ring-in 900ms cubic-bezier(.22,1,.36,1)',
      },
    },
  },
  plugins: [],
}
