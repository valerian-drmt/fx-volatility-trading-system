import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../tests/mocks/handlers";
import { useDeskData } from "../deskData";
import { makeFresh, statusFor } from "../freshness";
import { adaptConfig, adaptConfigCurrent, adaptConfigHistory } from "../live/config";
import { adaptPca } from "../live/pca";
import {
  adaptAccount as adaptPortfolioAccount,
  adaptDailyPnl,
  adaptPerfStats,
  adaptVegaPerTenor,
  adaptWaterfallGreek,
  deriveBookComposition,
} from "../live/portfolio";
import { adaptIvSurface } from "../live/surface";
import { adaptSystem } from "../live/system";
import { adaptTermStructure } from "../live/termStructure";
import { adaptAccount, adaptCash, adaptEvents, adaptLimits, adaptPositions, deriveNetGreeks } from "../live/trade";
import { DataProvider } from "../provider";

describe("freshness contract", () => {
  it("missing when asOf is null", () => {
    expect(statusFor(null, 1000)).toBe("missing");
    expect(makeFresh<number>(null, null, 1000)).toMatchObject({ status: "missing", ageMs: null });
  });
  it("live within the warn window, stale past it", () => {
    const now = 10_000;
    expect(statusFor(now - 500, 1000, now)).toBe("live");
    expect(statusFor(now - 2000, 1000, now)).toBe("stale");
  });
  it("makeFresh reports age and last value", () => {
    const f = makeFresh<string>("x", 9_000, 5_000, 10_000);
    expect(f).toMatchObject({ data: "x", status: "live", asOf: 9_000, ageMs: 1_000 });
  });
});

describe("adaptTermStructure", () => {
  it("maps atm/fair/rv from pillars and defaults bf/rr to 0", () => {
    const out = adaptTermStructure({
      symbol: "EURUSD",
      timestamp: "2026-06-16T00:00:00Z",
      pillars: [
        { tenor: "1M", dte: 30, sigma_atm_pct: 7.5, sigma_fair_q_pct: 8.1, rv_pct: 6.0 },
        { tenor: "2M", dte: 60, sigma_atm_pct: 7.8, sigma_fair_pct: 8.0, rv_pct: null },
      ],
    } as never);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ tenor: "1M", atm: 7.5, fair: 8.1, rv: 6.0, bf25: 0, rr10: 0 });
    expect(out[1]).toMatchObject({ tenor: "2M", atm: 7.8, fair: 8.0, rv: 0 });
  });
});

describe("adaptIvSurface", () => {
  it("extracts a 6×5 IV grid (%), maps deltas by position, missing → 0", () => {
    const grid = adaptIvSurface({
      surface: {
        "1M": { "10dp": { iv: 0.09 }, "25dp": { iv: 0.085 }, atm: { iv: 0.08 }, "25dc": { iv: 0.082 }, "10dc": { iv: 0.088 } },
        "3M": { atm: { iv: 0.075 } },
      },
    } as never);
    expect(grid).toHaveLength(6);
    [9, 8.5, 8, 8.2, 8.8].forEach((want, j) => expect(grid[0]![j]).toBeCloseTo(want, 6));
    // 3M: only atm present → others 0, atm = 7.5
    expect(grid[2]).toEqual([0, 0, 7.5, 0, 0]);
    // 6M absent entirely → all 0
    expect(grid[5]).toEqual([0, 0, 0, 0, 0]);
  });
});

describe("adaptPca", () => {
  const grid = Array.from({ length: 6 }, () => [0.1, 0.2, 0.3, 0.2, 0.1]);
  const state = {
    signals: {
      pc1: { z_score: -1.0, label: "FAIR", recommended_structure: "Long 1M ATM straddle" },
      pc2: { z_score: 0.8, label: "FAIR", recommended_structure: null },
      pc3: { z_score: -2.2, label: "CHEAP", recommended_structure: "fly", sub_signals: { convex_z: -2.2 } },
    },
    variance_explained: { pc1: 0.97, pc2: 0.012, pc3: 0.008, cumulative: 0.99 },
    loadings_stable: { pc1: true, pc2: true, pc3: false },
    loadings_grid: [grid, grid, grid],
    coherence: { all_coherent: true, contradictions: [] },
  };
  const model = { n_obs_used: 1500, variance_explained: [0.97, 0.012, 0.008, 0.005, 0.003, 0.002] };

  it("maps z/label/variance/tier/loadings and PC3 convex_z", () => {
    const data = adaptPca(state, model, [[], [], []]);
    expect(data.pcs).toHaveLength(3);
    expect(data.pcs[0]).toMatchObject({ id: "PC1", name: "level", z: -1.0, label: "FAIR", tier: 1, stable: true });
    expect(data.pcs[0]!.variance).toBeCloseTo(97, 6);
    expect(data.pcs[0]!.load).toEqual(grid);
    expect(data.pcs[2]).toMatchObject({ id: "PC3", label: "CHEAP", tier: 3, stable: false, dataQuality: "noisy" });
    expect(data.pcs[2]!.extra).toEqual({ convex_z: -2.2 });
  });

  it("derives pctile from the (reversed) z-history", () => {
    // pc1 history newest-first [-1.0,-0.5,0.2] → current z=-1.0 is the min → ~33%
    const data = adaptPca(state, model, [[{ z_score: -1.0 }, { z_score: -0.5 }, { z_score: 0.2 }], [], []]);
    expect(data.pcs[0]!.pctile).toBeCloseTo(100 / 3, 1);
    expect(data.pcs[0]!.zHistory).toEqual([0.2, -0.5, -1.0]); // oldest→newest
  });

  it("derives eigen gap/ratio from the model variance_explained list", () => {
    const data = adaptPca(state, model, [[], [], []]);
    expect(data.model.eigen.gap23).toBeCloseTo(0.4, 3); // 1.2% − 0.8%
    expect(data.model.eigen.ratio23).toBeCloseTo(1.5, 3); // 1.2 / 0.8
    expect(data.model.eigen.state).toBe("narrow"); // ratio < 2
    expect(data.model.variance.cumul).toBeCloseTo(99, 6);
    expect(data.model.pcaObs).toBe(1500);
  });
});

describe("adaptSystem", () => {
  const health = {
    status: "OK",
    components: { redis: "OK", database: "DOWN", engines: { market_data: "OK", vol_engine: "OK", risk_engine: "STALE" } },
  };

  it("prefers /dev/engines (5 engines + IB, heartbeat ages + status map)", () => {
    const dev = {
      engines: [
        { name: "market_data", status: "OK", hb_age_s: 1.2, stale_threshold_s: 60 },
        { name: "vol_engine", status: "STALE", hb_age_s: 320, stale_threshold_s: 300 },
        { name: "execution", status: "DOWN", hb_age_s: null, stale_threshold_s: 10 },
      ],
      ib_gateway: { status: "DOWN" },
    };
    const out = adaptSystem(health, dev);
    expect(out.engines).toHaveLength(4); // 3 + IB
    expect(out.engines[0]).toMatchObject({ name: "market-data", hb: 1.2, stale: 60, status: "up" });
    expect(out.engines[1]).toMatchObject({ name: "vol-engine", status: "warn" }); // STALE→warn
    expect(out.engines[2]).toMatchObject({ name: "exec-engine", hb: 0, status: "down" });
    expect(out.engines[3]).toMatchObject({ name: "IB Gateway", status: "down" });
    // DATA layer reflects component statuses (redis up, database down).
    const data = out.stack.find((l) => l.layer === "DATA")!;
    expect(data.items.find((i) => i.name === "redis")!.status).toBe("up");
    expect(data.items.find((i) => i.name === "postgres")!.status).toBe("down");
  });

  it("falls back to /health/extended engines when /dev is unavailable", () => {
    const out = adaptSystem(health, null);
    expect(out.engines.map((e) => e.name)).toEqual(["market-data", "vol-engine", "risk-engine"]);
    expect(out.engines[2]).toMatchObject({ name: "risk-engine", status: "warn" }); // STALE
  });
});

describe("adaptConfig", () => {
  const current = {
    version: 12,
    config: { signal: { pca: { z_threshold: 1.5 } }, sizing: { base_contracts: 25 }, debug: true },
    updated_by: "quant",
    comment: "x",
  };

  it("folds nested config into sections + flattens dotted keys", () => {
    const out = adaptConfigCurrent(current);
    expect(out.version).toBe(12);
    const signal = out.sections.find((s) => s.name === "signal")!;
    expect(signal.fields).toEqual([{ key: "pca.z_threshold", value: "1.5" }]);
    // scalar top-level keys land in "general"
    const general = out.sections.find((s) => s.name === "general")!;
    expect(general.fields).toContainEqual({ key: "debug", value: "true" });
  });

  it("maps history rows with fallbacks", () => {
    const rows = adaptConfigHistory([
      { version: 12, updated_by: "quant", comment: "c", updated_at: "2026-06-16T00:00:00Z" },
      { version: 11 },
    ]);
    expect(rows[0]).toEqual({ version: 12, by: "quant", comment: "c", at: "2026-06-16T00:00:00Z" });
    expect(rows[1]).toEqual({ version: 11, by: "—", comment: "", at: null });
  });

  it("adaptConfig bundles current + history", () => {
    const out = adaptConfig(current, [{ version: 12, updated_by: "quant" }]);
    expect(out.currentVersion).toBe(12);
    expect(out.sections.length).toBeGreaterThan(0);
    expect(out.history).toHaveLength(1);
  });
});

describe("adaptPositions / deriveNetGreeks / adaptAccount / adaptLimits / adaptEvents", () => {
  const now = Date.parse("2026-06-16T00:00:00Z");
  const raw = [
    {
      id: 7, package_id: "PK1", side: "SELL", quantity: 5, structure: "Straddle ATM 1M",
      expiry: "2026-07-16T00:00:00Z", current_pnl_usd: -120, nominal_eur: 1_000_000,
      delta_usd: 10, gamma_usd: 2, vega_usd: 3, theta_usd: -1, vanna_usd: 0.5, volga_usd: 0.2,
      iv: 8.1, market_price: 0.012, contract_price_entry: 0.011,
    },
  ];

  it("maps a position row + derives dte/pnlPct", () => {
    const pos = adaptPositions(raw, now);
    expect(pos[0]).toMatchObject({ id: "7", packageId: "PK1", side: "SELL", qty: 5, pnl: -120, delta: 10, vega: 3 });
    expect(pos[0]!.dte).toBe(30); // Jul 16 − Jun 16
    expect(pos[0]!.pnlPct).toBeCloseTo(-0.012, 6); // -120 / 1e6 * 100
  });

  it("net book greeks = Σ per-leg, non-net fields kept from mock", () => {
    const g = deriveNetGreeks(adaptPositions(raw, now));
    expect(g.netDelta).toBe(10);
    expect(g.netVega).toBe(3);
    expect(g.netUnreal).toBe(-120);
    expect(typeof g.var1d99).toBe("number"); // preserved from mock
  });

  it("account margin/excess from /trade/book", () => {
    const a = adaptAccount({ capital_total_usd: 1000, margin_used_usd: 250 });
    expect(a.marginInitPct).toBeCloseTo(25, 6);
    expect(a.excessLiq).toBe(750);
    expect(a.netLiq).toBe(1000);
  });

  it("limits keyed dict → struct with mock fallbacks", () => {
    const L = adaptLimits({ gamma: { value: 30000, unit: "$/pip" }, deltaBandUsd: { value: 6000 } });
    expect(L.gamma).toEqual({ cap: 30000, unit: "$/pip" });
    expect(L.deltaBandUsd).toBe(6000);
    expect(L.vega.cap).toBeGreaterThan(0); // fallback to mock
  });

  it("cash: usd_value → usd, drops unvalued currencies", () => {
    const c = adaptCash({
      currencies: [
        { ccy: "USD", settled: 500, unsettled: null, rate: 1, usd_value: 500 },
        { ccy: "EUR", settled: 200, unsettled: null, rate: 1.1, usd_value: 220 },
        { ccy: "JPY", settled: 1000, unsettled: null, rate: null, usd_value: null },
      ],
    });
    expect(c).toHaveLength(2); // JPY (no usd_value) dropped
    expect(c[0]).toMatchObject({ ccy: "USD", settled: 500, usd: 500 });
    expect(c[1]).toMatchObject({ ccy: "EUR", usd: 220, rate: 1.1 });
  });

  it("events map + validate impact, keep ISO date for the view parser", () => {
    const now = Date.parse("2026-06-30T12:30:00Z"); // 1 day before NFP
    const e = adaptEvents([
      { event_type: "NFP", impact: "high", region: "US", scheduled_at: "2026-07-01T12:30:00Z", source: "FRED", description: "Non-Farm Payrolls" },
      { event_type: "X", impact: "weird" },
    ], now);
    expect(e[0]).toMatchObject({ code: "NFP", impact: "high", country: "US", src: "FRED", date: "2026-07-01T12:30:00Z" });
    expect(e[0]!.in).toBe("1d 0h"); // relative countdown
    expect(e[1]!.impact).toBe("low"); // unknown → low
  });
});

describe("portfolio adapters", () => {
  it("account: margin %, deltas from prev_24h", () => {
    const a = adaptPortfolioAccount({
      latest: { net_liq_usd: 1000, cash_usd: 400, init_margin_req: 250, maint_margin_req: 150, excess_liquidity: 750, cushion: 0.7, open_positions_count: 6 },
      prev_24h: { net_liq_usd: 900, cash_usd: 380 },
    });
    expect(a.netLiq).toBe(1000);
    expect(a.marginInitPct).toBeCloseTo(25, 1);
    expect(a.marginMaintPct).toBeCloseTo(15, 1);
    expect(a.nPositions).toBe(6);
    expect(a.dNetLiq).toBeCloseTo(((1000 - 900) / 900) * 100, 1); // ~11.1%
    expect(a.dayPnl).toBeCloseTo(100, 1);
  });

  it("vega-per-tenor: vega_usd → $k", () => {
    const v = adaptVegaPerTenor([{ bucket: "1M", vega_usd: 6400, n_positions: 4 }]);
    expect(v[0]).toMatchObject({ tenor: "1M", vega: 6.4, n: 4 });
  });

  it("perf-stats: $→$k, hit_rate→%", () => {
    const ps = adaptPerfStats({ sharpe: 1.8, max_drawdown_pct: -8.2, current_drawdown_pct: -1.4, hit_rate: 0.58, cum_realized_usd: 312000, cum_unrealized_usd: 38400 });
    expect(ps).toMatchObject({ sharpe: 1.8, maxDd: -8.2, currentDd: -1.4 });
    expect(ps.cumRealized).toBe(312);
    expect(ps.cumUnrealized).toBeCloseTo(38.4, 1);
    expect(ps.hitRate).toBeCloseTo(58, 1);
  });

  it("daily-pnl: realized series → $k", () => {
    const d = adaptDailyPnl({ series: [{ realized_usd: 48000 }, { realized_usd: -4000 }] });
    expect(d).toEqual([48, -4]);
  });

  it("waterfall greek: totals → bridge steps in $k", () => {
    const w = adaptWaterfallGreek({ totals: { actual_pnl: 24900, gamma_pnl: 88200, vega_pnl: 54100, theta_pnl: -118400, delta_pnl: -5900, residual: 1300 } });
    const byLabel = Object.fromEntries(w.map((s) => [s.label, s.v]));
    expect(byLabel["+Γ"]).toBe(88.2);
    expect(byLabel["−Θ"]).toBe(-118.4);
    expect(byLabel["Net"]).toBe(24.9);
    expect(w[0]).toMatchObject({ label: "Start", type: "start" });
  });

  it("book composition: groups positions by structure, € → M, pct sums ~100", () => {
    const bc = deriveBookComposition([
      { structure: "Straddle ATM 1M", nominal: 6_000_000, vanna: 1, volga: 2 },
      { structure: "Straddle ATM 1M", nominal: 250_000, vanna: 1, volga: 2 },
      { structure: "Risk Reversal 25Δ", nominal: 7_500_000, vanna: 90, volga: 1 },
    ] as never);
    expect(bc.legs).toBe(3);
    const straddle = bc.byStructure.find((s) => s.name.startsWith("Straddle"))!;
    expect(straddle.legs).toBe(2);
    expect(straddle.nominal).toBeCloseTo(6.25, 2);
    expect(bc.byStructure.reduce((s, x) => s + x.pct, 0)).toBeCloseTo(100, 1);
  });
});

function TermProbe(): JSX.Element {
  const { termStructure } = useDeskData();
  return (
    <div>
      <span data-testid="status">{termStructure.status}</span>
      <span data-testid="n">{termStructure.data?.length ?? "none"}</span>
      <span data-testid="atm0">{termStructure.data?.[0]?.atm ?? "none"}</span>
    </div>
  );
}

function SurfaceProbe(): JSX.Element {
  const { surface } = useDeskData();
  return (
    <div>
      <span data-testid="s-status">{surface.status}</span>
      <span data-testid="s-iv00">{surface.data?.ivSurface[0]?.[0] ?? "none"}</span>
      {/* ivZ is the mock-backed gap — present in both mock and live */}
      <span data-testid="s-hasz">{surface.data?.ivZ.length ?? "none"}</span>
    </div>
  );
}

function PcaProbe(): JSX.Element {
  const { pca } = useDeskData();
  return (
    <div>
      <span data-testid="p-status">{pca.status}</span>
      <span data-testid="p-n">{pca.data?.pcs.length ?? "none"}</span>
      <span data-testid="p-z0">{pca.data?.pcs[0]?.z ?? "none"}</span>
    </div>
  );
}

function SystemProbe(): JSX.Element {
  const { system } = useDeskData();
  return (
    <div>
      <span data-testid="sys-status">{system.status}</span>
      <span data-testid="sys-eng">{system.data?.engines.length ?? "none"}</span>
      <span data-testid="sys-layers">{system.data?.stack.length ?? "none"}</span>
    </div>
  );
}

function ConfigProbe(): JSX.Element {
  const { config } = useDeskData();
  return (
    <div>
      <span data-testid="cfg-status">{config.status}</span>
      <span data-testid="cfg-v">{config.data?.currentVersion ?? "none"}</span>
      <span data-testid="cfg-sec">{config.data?.sections.length ?? "none"}</span>
    </div>
  );
}

function TradeProbe(): JSX.Element {
  const { trade } = useDeskData();
  return (
    <div>
      <span data-testid="tr-status">{trade.status}</span>
      <span data-testid="tr-pos">{trade.data?.positions.length ?? "none"}</span>
      <span data-testid="tr-netd">{trade.data?.greeks.netDelta ?? "none"}</span>
    </div>
  );
}

function PortfolioProbe(): JSX.Element {
  const { portfolio } = useDeskData();
  return (
    <div>
      <span data-testid="pf-status">{portfolio.status}</span>
      <span data-testid="pf-netliq">{portfolio.data?.account.netLiq ?? "none"}</span>
      <span data-testid="pf-wf">{portfolio.data?.waterfallGreek.length ?? "none"}</span>
    </div>
  );
}

describe("DataProvider swap", () => {
  it("mock mode serves synthetic term-structure (status live, no fetch)", () => {
    render(
      <DataProvider mock={true}>
        <TermProbe />
      </DataProvider>,
    );
    expect(screen.getByTestId("status").textContent).toBe("live");
    expect(Number(screen.getByTestId("n").textContent)).toBeGreaterThan(0);
  });

  it("mock mode serves synthetic surface (ivSurface + ivZ both present)", () => {
    render(
      <DataProvider mock={true}>
        <SurfaceProbe />
      </DataProvider>,
    );
    expect(screen.getByTestId("s-status").textContent).toBe("live");
    expect(screen.getByTestId("s-iv00").textContent).not.toBe("none");
    expect(Number(screen.getByTestId("s-hasz").textContent)).toBeGreaterThan(0);
  });

  it("live mode fetches + adapts the surface, keeps ivZ from the mock", async () => {
    server.use(
      http.get("*/api/v1/vol/surface", () =>
        HttpResponse.json({
          surface: { "1M": { "10dp": { iv: 0.11 }, atm: { iv: 0.1 } } },
        }),
      ),
      // provider fetches term-structure too (onUnhandledRequest: "error")
      http.get("*/api/v1/vol/term-structure", () =>
        HttpResponse.json({ symbol: "EURUSD", timestamp: "2026-06-16T00:00:00Z", pillars: [] }),
      ),
    );
    render(
      <DataProvider mock={false}>
        <SurfaceProbe />
      </DataProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("s-iv00").textContent).toBe("11"));
    expect(screen.getByTestId("s-status").textContent).toBe("live");
    // ivZ still served from the mock (5 tenor rows) — backend per-cell-z gap.
    expect(Number(screen.getByTestId("s-hasz").textContent)).toBeGreaterThan(0);
  });

  it("mock mode serves synthetic PCA (3 cards, status live)", () => {
    render(
      <DataProvider mock={true}>
        <PcaProbe />
      </DataProvider>,
    );
    expect(screen.getByTestId("p-status").textContent).toBe("live");
    expect(screen.getByTestId("p-n").textContent).toBe("3");
  });

  it("live mode fetches state/model/history + adapts the mode cards", async () => {
    server.use(
      http.get("*/api/v1/signals/pca/state", () =>
        HttpResponse.json({
          state: "stable",
          model_version: "v9",
          signals: {
            pc1: { z_score: -1.4, label: "FAIR", recommended_structure: null },
            pc2: { z_score: 0.5, label: "FAIR", recommended_structure: null },
            pc3: { z_score: -2.1, label: "CHEAP", recommended_structure: "fly" },
          },
          variance_explained: { pc1: 0.96, pc2: 0.02, pc3: 0.01, cumulative: 0.99 },
          loadings_stable: { pc1: true, pc2: true, pc3: false },
          loadings_grid: [],
          coherence: { all_coherent: true, contradictions: [] },
        }),
      ),
      http.get("*/api/v1/signals/pca/model", () =>
        HttpResponse.json({ active: true, version: "v9", n_obs_used: 1200, variance_explained: [0.96, 0.02, 0.01] }),
      ),
      http.get("*/api/v1/signals/pca/history", () => HttpResponse.json([{ z_score: -1.4 }, { z_score: -1.0 }])),
    );
    render(
      <DataProvider mock={false}>
        <PcaProbe />
      </DataProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("p-z0").textContent).toBe("-1.4"));
    expect(screen.getByTestId("p-n").textContent).toBe("3");
    expect(screen.getByTestId("p-status").textContent).toBe("live");
  });

  it("mock mode serves synthetic system (engines + 5 stack layers)", () => {
    render(
      <DataProvider mock={true}>
        <SystemProbe />
      </DataProvider>,
    );
    expect(screen.getByTestId("sys-status").textContent).toBe("live");
    expect(screen.getByTestId("sys-layers").textContent).toBe("5");
  });

  it("live mode composes the stack from health + dev engines", async () => {
    server.use(
      http.get("*/api/v1/health/extended", () =>
        HttpResponse.json({
          status: "OK",
          components: { redis: "OK", database: "OK", engines: { market_data: "OK" } },
        }),
      ),
      http.get("*/api/v1/dev/engines", () =>
        HttpResponse.json({
          engines: [{ name: "vol_engine", status: "OK", hb_age_s: 2, stale_threshold_s: 300 }],
          ib_gateway: { status: "OK" },
        }),
      ),
    );
    render(
      <DataProvider mock={false}>
        <SystemProbe />
      </DataProvider>,
    );
    // 1 dev engine + IB = 2 rows; 5 stack layers always.
    await waitFor(() => expect(screen.getByTestId("sys-eng").textContent).toBe("2"));
    expect(screen.getByTestId("sys-layers").textContent).toBe("5");
    expect(screen.getByTestId("sys-status").textContent).toBe("live");
  });

  it("mock mode serves synthetic portfolio (account + greek waterfall)", () => {
    render(
      <DataProvider mock={true}>
        <PortfolioProbe />
      </DataProvider>,
    );
    expect(screen.getByTestId("pf-status").textContent).toBe("live");
    expect(Number(screen.getByTestId("pf-netliq").textContent)).toBeGreaterThan(0);
    expect(Number(screen.getByTestId("pf-wf").textContent)).toBeGreaterThan(0);
  });

  it("live mode composes the portfolio (account net-liq from /portfolio/account)", async () => {
    server.use(
      http.get("*/api/v1/portfolio/account", () =>
        HttpResponse.json({
          latest: { net_liq_usd: 4_200_000, cash_usd: 1_500_000, init_margin_req: 1_800_000, maint_margin_req: 1_200_000, excess_liquidity: 2_900_000, cushion: 0.7, open_positions_count: 8 },
          prev_24h: { net_liq_usd: 4_180_000 },
        }),
      ),
    );
    render(
      <DataProvider mock={false}>
        <PortfolioProbe />
      </DataProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pf-netliq").textContent).toBe("4200000"));
    expect(screen.getByTestId("pf-status").textContent).toBe("live");
  });

  it("mock mode serves synthetic config (sections + version)", () => {
    render(
      <DataProvider mock={true}>
        <ConfigProbe />
      </DataProvider>,
    );
    expect(screen.getByTestId("cfg-status").textContent).toBe("live");
    expect(Number(screen.getByTestId("cfg-sec").textContent)).toBeGreaterThan(0);
  });

  it("live mode fetches + folds the versioned config", async () => {
    server.use(
      http.get("*/api/v1/admin/config", () =>
        HttpResponse.json({ version: 7, config: { surface: { calibration: "SSVI" } }, updated_by: "quant" }),
      ),
      http.get("*/api/v1/admin/config/history", () =>
        HttpResponse.json([{ version: 7, updated_by: "quant", comment: "no-arb", updated_at: "2026-06-16T00:00:00Z" }]),
      ),
    );
    render(
      <DataProvider mock={false}>
        <ConfigProbe />
      </DataProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("cfg-v").textContent).toBe("7"));
    expect(screen.getByTestId("cfg-status").textContent).toBe("live");
  });

  it("mock mode serves synthetic trade (positions + derived nets)", () => {
    render(
      <DataProvider mock={true}>
        <TradeProbe />
      </DataProvider>,
    );
    expect(screen.getByTestId("tr-status").textContent).toBe("live");
    expect(Number(screen.getByTestId("tr-pos").textContent)).toBeGreaterThan(0);
  });

  it("live mode fetches positions + derives the book net delta", async () => {
    server.use(
      http.get("*/api/v1/positions/open", () =>
        HttpResponse.json([
          { id: 1, side: "BUY", quantity: 2, delta_usd: 40, vega_usd: 5, current_pnl_usd: 10, expiry: "2026-08-01T00:00:00Z" },
          { id: 2, side: "SELL", quantity: 1, delta_usd: -15, vega_usd: 2, current_pnl_usd: -3, expiry: "2026-08-01T00:00:00Z" },
        ]),
      ),
    );
    render(
      <DataProvider mock={false}>
        <TradeProbe />
      </DataProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("tr-pos").textContent).toBe("2"));
    expect(screen.getByTestId("tr-netd").textContent).toBe("25"); // 40 − 15
    expect(screen.getByTestId("tr-status").textContent).toBe("live");
  });

  it("live mode fetches + adapts the backend term-structure", async () => {
    server.use(
      http.get("*/api/v1/vol/term-structure", () =>
        HttpResponse.json({
          symbol: "EURUSD",
          timestamp: "2026-06-16T00:00:00Z",
          pillars: [{ tenor: "1M", dte: 30, sigma_atm_pct: 9.9, rv_pct: 7 }],
        }),
      ),
    );
    render(
      <DataProvider mock={false}>
        <TermProbe />
      </DataProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("atm0").textContent).toBe("9.9"));
    expect(screen.getByTestId("status").textContent).toBe("live");
  });
});
