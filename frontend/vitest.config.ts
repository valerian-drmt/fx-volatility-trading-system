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
      include: ["src/api/**", "src/hooks/**", "src/store/**", "src/components/**"],
      // Visual-shell components are exercised by the Playwright e2e suite
      // (R5 PR #8), not by Vitest. Exclude them from the coverage target
      // so they don't drag the line ratio below the 70% threshold without
      // reflecting a real test gap.
      exclude: [
        "src/components/layout/**",
        "src/components/charts/**",
        "src/components/panels/ChartPanel.tsx",
        "src/components/panels/LogsPanel.tsx",
        "src/components/panels/SmileChartPanel.tsx",
        "src/components/panels/StatusPanel.tsx",
        "src/components/panels/TermStructurePanel.tsx",
        "src/components/panels/VolScannerPanel.tsx",
        "src/hooks/useRiskStream.ts",
        "src/hooks/useSystemAlerts.ts",
      ],
      // R8 PR #3 : gate the frontend CI job on a 70% line threshold so the
      // suite can't silently rot when new components land without tests.
      thresholds: {
        lines: 70,
      },
    },
  },
});
