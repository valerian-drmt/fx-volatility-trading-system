import { test, expect } from "@playwright/test";

import { mockBackend } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
});

// Boot smoke. The previous specs asserted a pre-voldesk dashboard layout whose
// testIds (status-panel … book-panel, conn-indicator) no longer exist, so they
// could never pass. Until a full voldesk e2e is written, assert the app boots
// and renders its static header shell (rendered unconditionally in Header.tsx),
// which proves the bundle loads and mounts without crashing.
test("app boots and renders the header shell", async ({ page }) => {
  await page.goto("/fx-volatility-trading-system/");

  await expect(page.getByTestId("app-header")).toBeVisible();
  await expect(page.getByRole("heading", { name: /FX Vol Dashboard/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Dev/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Parameter/ })).toBeVisible();
});
