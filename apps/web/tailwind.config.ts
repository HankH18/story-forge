import type { Config } from 'tailwindcss'

/* Style guide §12. These names map 1:1 onto the CSS variables in
   src/styles/tokens.css so utilities and raw CSS never drift.
   Tailwind v3 — see docs/DEVIATIONS.md 0.3 for why not v4. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: {
          page: '#FDF6E7', card: '#FFFBF0', warm: '#F6EAD2',
          shell: '#F1E4C8', rule: '#EADCBE', shade: '#E3D3B2',
        },
        ink: { 900: '#2B2118', 700: '#4A3A2A', 500: '#6F5B45', 300: '#A08A6E' },
        gold: { 300: '#F2D48A', 500: '#D8A93C', 700: '#A87A22' },
        berry: { 100: '#F8E3DF', 500: '#C0453B', 700: '#92302A' },
        leaf: { 100: '#E7F0E2', 500: '#4C7C51', 700: '#35603A' },
        sky: { 100: '#E2EDF2', 500: '#3D7891', 700: '#2A5A70' },
        amber: { 100: '#FBEFD4', 500: '#C98A2E', 700: '#96631A' },
      },
      fontFamily: {
        display: ['Bree Serif', 'Georgia', 'serif'],
        ui: ['Nunito', 'system-ui', 'sans-serif'],
        read: ['Andika', 'Nunito', 'sans-serif'],
        mono: ['IBM Plex Mono', 'monospace'],
        hand: ['Kalam', 'cursive'],
      },
      fontSize: {
        read: ['34px', { lineHeight: '1.65', letterSpacing: '0.01em' }],
        'read-lg': ['40px', { lineHeight: '1.9', letterSpacing: '0.01em' }],
      },
      borderRadius: { chip: '10px', btn: '16px', card: '22px' },
      boxShadow: {
        'lift-kid': '0 6px 0 #2B2118',
        'lift-kid-down': '0 2px 0 #2B2118',
        'lift-parent': '0 4px 0 #E3D3B2',
        'focus-gold': '0 0 0 4px #F2D48A',
      },
      minHeight: { tap: '48px', 'tap-kid': '64px' },
      // §11 targets are a floor in BOTH axes: a 64px-tall, 30px-wide button is
      // not a 64px target. Same two token values as minHeight, so `min-w-tap`
      // and `min-h-tap` can never drift apart.
      minWidth: { tap: '48px', 'tap-kid': '64px' },
      transitionTimingFunction: {
        page: 'cubic-bezier(.2,.7,.2,1)',
        pop: 'cubic-bezier(.2,.8,.3,1.2)',
      },
    },
  },
  plugins: [],
} satisfies Config
