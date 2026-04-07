/**
 * @description Auto-generated core module. Do not mutate directly.
 */
import js from "@eslint/js";
import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";
import vueParser from "vue-eslint-parser";
import zen70Plugin from "./eslint-plugin-zen70.js";

export default [
  { ignores: ["coverage/**", "dist/**", "eslint.config.js", "eslint-plugin-zen70.js", "vite.config.ts", "*.config.js", "*.config.ts", "tests/**"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  ...vue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        ecmaVersion: "latest",
        sourceType: "module",
        project: ["./tsconfig.json"],
        extraFileExtensions: [".vue"],
      },
    },
    plugins: { zen70: zen70Plugin },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": ["error", { prefer: "type-imports" }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "zen70/no-bare-fetch": "error",
      "zen70/no-hardcoded-api": "error",
    },
  },
  {
    files: ["**/*.ts"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        project: ["./tsconfig.json"],
      },
    },
    plugins: { zen70: zen70Plugin },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": ["error", { prefer: "type-imports" }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "zen70/no-bare-fetch": "error",
      "zen70/no-hardcoded-api": "error",
    },
  },
];
