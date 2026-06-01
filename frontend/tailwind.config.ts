import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./hooks/**/*.{js,ts,jsx,tsx,mdx}",
    "./services/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        "vol-green":  "#22c55e",
        "vol-yellow": "#eab308",
        "vol-orange": "#f97316",
        "vol-red":    "#ef4444",
        "surface":    "#0f1117",
        "surface-2":  "#1a1d2e",
        "surface-3":  "#242740",
        "border":     "#2d3148",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
