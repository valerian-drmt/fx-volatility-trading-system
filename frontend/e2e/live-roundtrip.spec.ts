import { expect, test } from "@playwright/test";

/**
 * Unmocked round trip (#341, audit 07#15). No route is intercepted: the
 * compose job seeds Redis through the real bus code (scripts/e2e/seed_market.py:
 * `publish_tick` and the vol engine's latest-surface key, from the canonical
 * payload tests/fixtures/payloads/vol_update.json), and the desk must show those
 * values after they cross api -> WebSocket bridge -> nginx -> browser.
 *
 * Runs only against a seeded stack (E2E_LIVE=1, set by the compose CI job); the
 * preview job serves the bundle alone and keeps the mocked boot smoke.
 */
test.skip(process.env["E2E_LIVE"] !== "1", "needs the seeded docker-compose stack");

// The values the seed publishes (ci.yml frontend-e2e-compose) and the fixture holds.
const BID = "1.12345";
const ASK = "1.12347";
const MID = "1.12346";
const ATM_1M_IV = "6.3"; // vol_update.json: surface["1M"].atm.iv = 0.0628

test("a published tick reaches the top bar through the WebSocket bridge", async ({ page }) => {
  await page.goto("/fx-volatility-trading-system/");
  await expect(page.locator(".ticker-mid")).toContainText(MID, { timeout: 20_000 });
  await expect(page.locator(".ticker-ba")).toHaveText(`${BID} / ${ASK}`);
});

test("the vol engine's surface payload renders in the Signals grid", async ({ page }) => {
  await page.goto("/fx-volatility-trading-system/#signals");
  const cell = page.locator(".gz-cell", { has: page.locator(".gz-iv") }).filter({ hasText: ATM_1M_IV }).first();
  await expect(cell).toBeVisible({ timeout: 20_000 });
  await expect(cell).toHaveAttribute("title", /^1M ATM · IV 6\.3 /);
});
