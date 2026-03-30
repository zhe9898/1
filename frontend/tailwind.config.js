/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,js,ts}"],
  theme: { extend: {} },
  plugins: [require("daisyui")],
  daisyui: {
    themes: ["dark", "light", "synthwave", "cyberpunk", "luxury", "dracula"],
  },
};

