/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/src/**/*.{ts,tsx,html}', './src/renderer/index.html'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
        mono: ['"SF Mono"', '"Fira Code"', '"Cascadia Code"', 'monospace']
      },
      colors: {
        accent: {
          DEFAULT: '#a7f3d0',
          hover: '#6ee7b7',
          soft: '#f0fdf4',
          text: '#047857'
        },
        sidebar: {
          bg: '#f8fafc',
          text: '#475569',
          'text-dim': '#94a3b8',
          'active-bg': 'rgba(167, 243, 208, 0.4)',
          'active-text': '#047857',
          hover: '#f1f5f9',
          divider: '#e2e8f0',
          border: '#e2e8f0'
        },
        surface: {
          app: '#e2e8f0',
          main: '#f8fafc',
          card: '#ffffff',
          input: '#ffffff'
        },
        chat: {
          user: {
            bg: '#ecfdf5',
            text: '#064e3b'
          },
          ai: {
            bg: '#ffffff',
            border: '#e2e8f0'
          }
        }
      },
      borderRadius: {
        sm: '6px',
        DEFAULT: '10px',
        lg: '14px',
        xl: '18px'
      }
    }
  },
  plugins: []
}
