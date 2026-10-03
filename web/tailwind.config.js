/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
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
        // Restrained neon accents.
        aqua: {
          300: '#7DF3E1',
          400: '#38E0C8',
          500: '#16C7AE',
          600: '#0EA393',
        },
        violet: {
          300: '#C4B5FD',
          400: '#A78BFA',
          500: '#8B5CF6',
        },
        amber: {
          300: '#FCD34D',
          400: '#FBBF24',
          500: '#F59E0B',
        },
        rose: {
          400: '#FB7185',
          500: '#F43F5E',
        },
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
