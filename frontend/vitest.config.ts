/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/tests/setup.ts"],
    exclude: ["node_modules", "dist", "e2e/**"],
    css: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      // Honest denominator: ALL source (voldesk + pages included). The old
      // whitelist measured only api/hooks/store/components, so the 70% gate
      // never saw the actual app (~10k LOC of voldesk).
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/tests/**",        // vitest setup
        "src/main.tsx",
        "src/vite-env.d.ts",
        "src/plotly-shim.d.ts",
        "src/api/schema.d.ts", // generated
        "src/**/__tests__/**",
      ],
      // Measured honest baseline with the full denominator: 17% (2026-07-18).
      // Floor set ~2 points below; ratchet upward as view/component tests
      // land — never lower it.
      thresholds: {
        lines: 15,
      },
    },
  },
});
