/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#151f1d",
          soft: "#374644",
          muted: "#5c6d6a",
        },
        accent: {
          DEFAULT: "#0e6b5b",
          dark: "#0b564a",
          wash: "#e4f0ed",
        },
        ground: "#f7faf9",
        line: "#dce5e3",
        warn: "#8f6408",
        danger: "#9c3527",
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
