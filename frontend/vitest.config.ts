/**
 * @description Vitest coverage gate for frontend CI.
 */
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.spec.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "cobertura"],
      reportsDirectory: "coverage",
      include: ["src/**/*.{ts,vue}"],
      exclude: [
        "src/main.ts",
        "src/router/**",
        "src/types/**",
      ],
      thresholds: {
        lines: 57,
        functions: 68,
        branches: 60,
        statements: 57,
      },
    },
  },
});
