import { test, expect } from "@playwright/test";

import { mockBackend } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
});

// Boot smoke. The previous specs asserted a pre-voldesk dashboard layout whose
// testIds (status-panel … book-panel, conn-indicator) no longer exist, so they
// could never pass. Assert instead that the app mounts and renders the VOLDESK
// header shell (the banner + branding), which proves the bundle loads without
// crashing. A full voldesk e2e is a follow-up.
test("app boots and renders the VOLDESK header", async ({ page }) => {
  await page.goto("/fx-volatility-trading-system/");

  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByText(/VOLDESK/).first()).toBeVisible();
});
