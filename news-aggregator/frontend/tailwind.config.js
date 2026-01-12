/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        liga: {
          50: '#f0f9ff',
          100: '#e0f2fe',
          200: '#bae6fd',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0284c7',
          600: '#0369a1',
          700: '#075985',
          800: '#0c4a6e',
          900: '#0a3d5c'
        },
        priority: {
          high: '#dc2626',     // red - high priority
          medium: '#ea580c',   // orange - medium priority
          low: '#ca8a04',      // yellow - low priority
          none: '#3b82f6'      // blue - not relevant
        }
      }
    }
  },
  plugins: []
}
