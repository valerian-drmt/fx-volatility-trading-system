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
];

export const server = setupServer(...handlers);
