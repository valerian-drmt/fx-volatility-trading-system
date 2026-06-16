import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../tests/mocks/handlers";
import { useDeskData } from "../deskData";
import { makeFresh, statusFor } from "../freshness";
import { adaptPca } from "../live/pca";
import { adaptIvSurface } from "../live/surface";
import { adaptSystem } from "../live/system";
import { adaptTermStructure } from "../live/termStructure";
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
