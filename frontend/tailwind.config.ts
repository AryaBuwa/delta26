import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Sora', 'system-ui', 'sans-serif'],
        mono: ['DM Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        bg: {
          base:    '#0a0b0d',
          raised:  '#111318',
          surface: '#181c24',
          overlay: '#1e2230',
        },
        line: {
          dim:    'rgba(255,255,255,0.07)',
          soft:   'rgba(255,255,255,0.14)',
          hard:   'rgba(255,255,255,0.25)',
        },
        text: {
          primary: '#f0f0ee',
          muted:   'rgba(240,240,238,0.55)',
          dim:     'rgba(240,240,238,0.28)',
        },
        accent: {
          DEFAULT: '#e8ff47',
          blue:    '#47b3ff',
          red:     '#ff4747',
          green:   '#47d18c',
          orange:  '#ff8c47',
        },
      },
      borderRadius: {
        sm: '2px',
        DEFAULT: '3px',
        md: '4px',
        lg: '6px',
      },
      fontSize: {
        '2xs': ['10px', { letterSpacing: '0.12em' }],
        xs:    ['11px', { letterSpacing: '0.06em' }],
        sm:    ['12px', { lineHeight: '1.5' }],
        base:  ['13px', { lineHeight: '1.6' }],
        md:    ['14px', { lineHeight: '1.6' }],
        lg:    ['16px', { lineHeight: '1.5' }],
        xl:    ['20px', { lineHeight: '1.3' }],
        score: ['52px', { letterSpacing: '-0.02em', lineHeight: '1' }],
        hero:  ['72px', { letterSpacing: '-0.04em', lineHeight: '0.9' }],
      },
      animation: {
        'pulse-dot': 'pulse-dot 1.4s ease-in-out infinite',
        'slide-up':  'slide-up 0.3s ease-out',
        'fade-in':   'fade-in 0.2s ease-out',
      },
      keyframes: {
        'pulse-dot': {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.25' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}

export default config
