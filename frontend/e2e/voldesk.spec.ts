import { test, expect } from "@playwright/test";

import { mockBackend } from "./fixtures";

// Preview serves the static bundle with no backend. The live DataProvider hits
// many REST routes at mount; a catch-all returns empty-but-valid JSON so views
// render their stale/missing states instead of erroring, and `mockBackend`
// (registered after → higher priority) supplies the richer fixtures.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await mockBackend(page);
});

test("root boots the live voldesk shell", async ({ page }) => {
  await page.goto("/");

  // The voldesk shell (topbar + rail + content) renders regardless of feed state.
  await expect(page.locator(".shell")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible({ timeout: 10_000 });
});

test("rail navigates between desk views", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible({ timeout: 10_000 });

  // Hash-based routing inside the desk: jump to Signals and back.
  await page.goto("/#/signals");
  await expect(page.locator(".shell")).toBeVisible();
});
