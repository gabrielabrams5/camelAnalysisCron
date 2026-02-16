/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: "#0D121D",
        gold: "#DFCB63",
        slate: "#1E293B",
        muted: "#94A3B8",
        border: "#334155",
      },
      borderRadius: {
        card: "8px",
      },
    },
  },
  plugins: [],
};
