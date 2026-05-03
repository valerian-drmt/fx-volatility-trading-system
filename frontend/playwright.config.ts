import { defineConfig, devices } from "@playwright/test";

const PORT = 4173;
const BASE_URL = process.env["PLAYWRIGHT_BASE_URL"] ?? `http://localhost:${PORT}`;

// When PLAYWRIGHT_BASE_URL is set (docker stack, R6), skip the local webServer —
// we assume the URL is already serving the built bundle.
const useExternalServer = !!process.env["PLAYWRIGHT_BASE_URL"];

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  retries: process.env["CI"] ? 1 : 0,
  reporter: process.env["CI"] ? [["list"], ["github"]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: useExternalServer
    ? undefined
    : {
        command: "npm run preview",
        url: BASE_URL,
        reuseExistingServer: !process.env["CI"],
        timeout: 30_000,
      },
});
