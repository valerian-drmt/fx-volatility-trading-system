import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../tests/mocks/handlers";
import { useDeskData } from "../deskData";
import { makeFresh, statusFor } from "../freshness";
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
