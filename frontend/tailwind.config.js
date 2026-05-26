/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        terminal: {
          bg:       '#0d1117',
          surface:  '#161b22',
          border:   '#30363d',
          muted:    '#6e7681',
          text:     '#e6edf3',
          green:    '#3fb950',
          red:      '#f85149',
          orange:   '#d29922',
          blue:     '#58a6ff',
          purple:   '#bc8cff',
          accent:   '#1f6feb',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Courier New', 'monospace'],
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
