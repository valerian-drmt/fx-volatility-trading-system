import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

// REST mocks reused across unit + store tests. Hooks/panels PRs extend this list.
export const handlers = [
  http.get("*/api/v1/health", () => HttpResponse.json({ status: "OK" })),

  http.get("*/api/v1/vol/surface", ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json({
      symbol: url.searchParams.get("symbol") ?? "EURUSD",
      snapshot_ts: "2026-04-20T12:00:00Z",
      tenors: ["1W", "1M"],
      strikes: [1.07, 1.08, 1.09],
      vols: [
        [0.075, 0.072, 0.074],
        [0.080, 0.078, 0.079],
      ],
    });
  }),

  http.post("*/api/v1/price", async ({ request }) => {
    const body = (await request.json()) as { spot: number; volatility: number };
    return HttpResponse.json({ price: body.spot * body.volatility });
  }),

  // Desk live-data defaults (R11) — the voldesk provider fetches these on mount
  // in live mode. Minimal valid payloads; tests override with server.use(...).
  http.get("*/api/v1/vol/term-structure", () =>
    HttpResponse.json({ symbol: "EURUSD", timestamp: "2026-06-16T00:00:00Z", pillars: [] }),
  ),
  http.get("*/api/v1/signals/pca/state", () =>
    HttpResponse.json({ state: "stable", model_version: "test", signals: {} }),
  ),
  http.get("*/api/v1/signals/pca/model", () =>
    HttpResponse.json({ active: true, version: "test", variance_explained: null }),
  ),
  http.get("*/api/v1/signals/pca/history", () => HttpResponse.json([])),
  http.get("*/api/v1/health/extended", () =>
    HttpResponse.json({
      status: "OK",
      components: { redis: "OK", database: "OK", engines: { market_data: "OK", vol_engine: "OK", risk_engine: "OK" } },
    }),
  ),
  http.get("*/api/v1/dev/engines", () =>
    HttpResponse.json({ engines: [], ib_gateway: { status: "OK" } }),
  ),
  http.get("*/api/v1/admin/config", () =>
    HttpResponse.json({ version: 0, config: {}, updated_at: "2026-06-16T00:00:00Z", updated_by: null, comment: null }),
  ),
  http.get("*/api/v1/admin/config/history", () => HttpResponse.json([])),
  http.get("*/api/v1/positions/open", () => HttpResponse.json([])),
  http.get("*/api/v1/trade/limits", () => HttpResponse.json({})),
  http.get("*/api/v1/trade/book", () => HttpResponse.json({ capital_total_usd: 0, margin_used_usd: 0 })),
  http.get("*/api/v1/regime/events", () => HttpResponse.json([])),
  http.get("*/api/v1/portfolio/cash", () =>
    HttpResponse.json({ currencies: [], total_usd: 0, eurusd_spot: null, freshness: "missing" }),
  ),
  http.get("*/api/v1/portfolio/account", () =>
    HttpResponse.json({ latest: null, prev_24h: null, freshness: "missing" }),
  ),
  http.get("*/api/v1/portfolio/vega-per-tenor", () => HttpResponse.json([])),
  http.get("*/api/v1/portfolio/stats", () =>
    HttpResponse.json({ sharpe: null, max_drawdown_pct: null, current_drawdown_pct: null, hit_rate: null, cum_realized_usd: 0, cum_unrealized_usd: 0, n_closed: 0, n_open: 0, n_days: 0 }),
  ),
  http.get("*/api/v1/portfolio/daily-pnl", () => HttpResponse.json({ days: 90, series: [], total_realized_usd: 0 })),
  http.get("*/api/v1/portfolio/pnl-attribution", () => HttpResponse.json({ totals: {}, per_position: [] })),
  http.get("*/api/v1/portfolio/equity-curve", () => HttpResponse.json([])),
];

export const server = setupServer(...handlers);
