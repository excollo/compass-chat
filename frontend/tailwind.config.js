/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          900: '#0f172a',
          800: '#1e293b',
        },
        accent: {
          green: '#16a34a',
        }
      },
      fontFamily: {
        sans: ['IBM Plex Sans', 'DM Sans', 'sans-serif'],
      }
    },
  },
  plugins: [],
}
