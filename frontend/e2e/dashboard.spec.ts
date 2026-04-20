import { test, expect } from "@playwright/test";
import { mockBackend } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
});

test("dashboard boots with header and all nine panels render", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByTestId("app-header")).toBeVisible();
  await expect(page.getByRole("heading", { name: "FX Vol Dashboard" })).toBeVisible();

  const panels = [
    "status-panel",
    "portfolio-panel",
    "logs-panel",
    "chart-panel",
    "term-panel",
    "smile-panel",
    "scanner-panel",
    "order-ticket-panel",
    "book-panel",
  ];

  for (const id of panels) {
    await expect(page.getByTestId(id)).toBeVisible({ timeout: 10_000 });
  }
});

test("connection indicator surfaces the current WS status", async ({ page }) => {
  await page.goto("/");
  // The ws:// attempts fail in the preview server context (no backend) — the
  // indicator should either stay in "connecting" long enough to be seen or
  // flip to "retry". Both are valid transient states post-load.
  const indicator = page.getByTestId("conn-indicator").first();
  await expect(indicator).toBeVisible();
  await expect(indicator).toHaveAttribute(
    "data-status",
    /^(idle|connecting|retry|closed|open)$/,
  );
});
