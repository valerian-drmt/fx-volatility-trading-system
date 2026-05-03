import { test, expect } from "@playwright/test";
import { mockBackend } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
  await page.goto("/");
});

test("order ticket previews greeks once strike and tenor are filled", async ({ page }) => {
  const ticket = page.getByTestId("order-ticket-panel");
  await expect(ticket).toBeVisible();

  // Hint is visible up-front.
  await expect(ticket.getByText(/fill side, strike and tenor/)).toBeVisible();

  await ticket.getByLabel("Strike").fill("1.085");
  await ticket.getByLabel("Tenor").fill("1M");

  // Greeks tiles appear after /greeks resolves.
  await expect(ticket.getByTestId("ticket-greeks")).toBeVisible();
  await expect(ticket.getByTestId("metric-Delta")).toContainText("0.520");
  await expect(ticket.getByTestId("metric-Gamma")).toContainText("4.200");
});

test("submit button is disabled until the draft is valid", async ({ page }) => {
  const ticket = page.getByTestId("order-ticket-panel");
  const submit = ticket.getByRole("button", { name: /submit/i });

  await expect(submit).toBeDisabled();

  await ticket.getByLabel("Strike").fill("1.08");
  await ticket.getByLabel("Tenor").fill("1M");

  await expect(submit).toBeEnabled();
});
