// ESLint 10 flat config (#308): the eslintrc format is gone in v9+. Same rule
// set as the old .eslintrc.cjs, through typescript-eslint v8 and the flat
// presets of react-hooks v7 / react-refresh.
import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import prettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist", "coverage", "playwright-report", "test-results", "vite.config.ts", "vite.config.js", "vite.config.d.ts"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      // react-hooks v7: the classic hook rules plus the React Compiler rules
      // (refs, set-state-in-effect, purity, immutability, error-boundaries), #404.
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      prettier,
    ],
    languageOptions: { ecmaVersion: 2022, sourceType: "module", globals: globals.browser },
    rules: {
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
  // The entry file mounts the app and exports nothing; Fast Refresh does not apply.
  { files: ["src/main.tsx"], rules: { "react-refresh/only-export-components": "off" } },
]);
