import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../tests/mocks/handlers";
import { useDeskData } from "../deskData";
import { makeFresh, statusFor } from "../freshness";
import { adaptIvSurface } from "../live/surface";
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
