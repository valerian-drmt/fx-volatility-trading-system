import type { Page } from "@playwright/test";

// Preview builds serve the bundle as static assets — the backend is not reachable
// in this context. We intercept the same REST routes the panels would hit in prod
// and return deterministic fixtures so each spec asserts on known values.

const POSITIONS = [
  {
    id: 1,
    symbol: "EURUSD",
    instrument_type: "OPT",
    side: "BUY",
    quantity: "10",
    strike: "1.08",
    maturity: "2026-05-15",
    option_type: "CALL",
    entry_price: "0.0012",
    entry_timestamp: "2026-04-01T10:00:00Z",
    status: "OPEN",
  },
  {
    id: 2,
    symbol: "EURUSD",
    instrument_type: "OPT",
    side: "SELL",
    quantity: "5",
    strike: "1.09",
    maturity: "2026-04-30",
    option_type: "PUT",
    entry_price: "0.0008",
    entry_timestamp: "2026-03-15T10:00:00Z",
    status: "CLOSED",
  },
];

const SIGNALS = [
  {
    id: 1,
    timestamp: "2026-04-20T09:00:00Z",
    underlying: "EURUSD",
    tenor: "1M",
    dte: 30,
    sigma_mid: "0.075",
    sigma_fair: "0.072",
    ecart: "0.003",
    signal_type: "CHEAP",
    rv: "0.070",
  },
];

const TERM_STRUCTURE = {
  symbol: "EURUSD",
  timestamp: "2026-04-20T09:00:00Z",
  pillars: [
    { tenor: "1W", dte: 7, sigma_atm_pct: 0.068 },
    { tenor: "1M", dte: 30, sigma_atm_pct: 0.075 },
    { tenor: "3M", dte: 90, sigma_atm_pct: 0.081 },
  ],
};

const SMILE = {
  symbol: "EURUSD",
  timestamp: "2026-04-20T09:00:00Z",
  tenor: "1M",
  dte: 30,
  points: [
    { strike: 1.07, iv_pct: 0.08, delta_label: "25D" },
    { strike: 1.08, iv_pct: 0.075, delta_label: "ATM" },
    { strike: 1.09, iv_pct: 0.079, delta_label: "-25D" },
  ],
};

const GREEKS = { price: 0.00123, delta: 0.52, gamma: 4.2, vega: 0.0021, theta: -0.00005 };

/** Intercept every REST route the panels use and return fixtures. */
export async function mockBackend(page: Page): Promise<void> {
  await page.route("**/api/v1/positions*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(POSITIONS) }),
  );
  await page.route("**/api/v1/signals*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SIGNALS) }),
  );
  await page.route("**/api/v1/vol/term-structure*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TERM_STRUCTURE) }),
  );
  await page.route("**/api/v1/vol/smile/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SMILE) }),
  );
  await page.route("**/api/v1/vol/surface*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        symbol: "EURUSD",
        timestamp: "2026-04-20T09:00:00Z",
        surface: {},
      }),
    }),
  );
  await page.route("**/api/v1/greeks*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(GREEKS) }),
  );
  await page.route("**/api/v1/risk*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
  );
  await page.route("**/api/v1/health*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "OK" }) }),
  );
}
