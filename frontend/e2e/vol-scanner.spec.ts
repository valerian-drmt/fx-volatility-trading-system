import { test, expect } from "@playwright/test";
import { mockBackend } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
  await page.goto("/");
});

test("vol scanner renders the latest signals", async ({ page }) => {
  const scanner = page.getByTestId("scanner-panel");
  await expect(scanner).toBeVisible();

  // CHEAP signal from the fixture should show up tagged.
  await expect(scanner.getByText("CHEAP")).toBeVisible();
  await expect(scanner.getByText("EURUSD").first()).toBeVisible();
});

test("book panel splits open vs closed positions with side coloring", async ({ page }) => {
  const book = page.getByTestId("book-panel");
  await expect(book).toBeVisible();
  await expect(book.getByText(/1 open · 1 closed/)).toBeVisible();

  const buyCell = book.getByText("BUY").first();
  const sellCell = book.getByText("SELL").first();
  await expect(buyCell).toHaveAttribute("data-sign", "pos");
  await expect(sellCell).toHaveAttribute("data-sign", "neg");
});
