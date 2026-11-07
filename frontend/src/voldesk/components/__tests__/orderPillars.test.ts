/**
 * #379 — the Strike Δ button and the "Legs on the smile" column must agree,
 * for every product, at a live spot far from the mock preview spot.
 */
import { describe, expect, it } from "vitest";
import { DATA } from "../../data";
import { MOCK_RESOLVER, buildLegs, gridStrike, ladderColumn, livePillarStrike, type StrikeResolver } from "../orderPillars";

const PILLARS = DATA.deltas;
const mockStrike = MOCK_RESOLVER.strike;

// live spot ~400 pips above the mock preview spot (1.0842), as on prod
const LIVE_ATM = gridStrike(1.1240);
const liveStrike = (t: string, p: string): number => livePillarStrike(LIVE_ATM, t, p, mockStrike);
const live: StrikeResolver = { strike: liveStrike, iv: MOCK_RESOLVER.iv, spot: 1.124 };

describe("livePillarStrike", () => {
  it("keeps the mock smile's shape but sits on the live level, on the CME grid", () => {
    expect(liveStrike("3M", "ATM")).toBe(LIVE_ATM);
    const put = liveStrike("3M", "25Δp");
    const call = liveStrike("3M", "25Δc");
    expect(put).toBeLessThan(LIVE_ATM);
    expect(call).toBeGreaterThan(LIVE_ATM);
    expect(Math.abs((put / 0.005) - Math.round(put / 0.005))).toBeLessThan(1e-6);
    // the offsets are the mock smile's own
    expect(call - LIVE_ATM).toBeCloseTo(
      gridStrike(1.1240 + mockStrike("3M", "25Δc") - mockStrike("3M", "ATM")) - LIVE_ATM, 10,
    );
  });
});

describe("ladder column == pressed pillar (every product)", () => {
  it("a vanilla put / call lands under the button that was pressed", () => {
    for (const product of ["Vanilla Put", "Vanilla Call"]) {
      PILLARS.forEach((w, i) => {
        const strike = liveStrike("3M", w); // what the button sets
        const [leg] = buildLegs(product, "BUY", "3M", "6M", strike, 10, w, "call", "ATM", "25Δ", live);
        expect(ladderColumn(leg!.strike, leg!.tenor, PILLARS, liveStrike)).toBe(i);
      });
    }
  });

  it("the OLD placement (mock pillars) put every live leg in the last column", () => {
    const strike = liveStrike("3M", "25Δp");
    expect(ladderColumn(strike, "3M", PILLARS, mockStrike)).toBe(PILLARS.length - 1);
  });

  it("every structured product's legs sit on live pillar strikes, each under its own column", () => {
    const products = ["Butterfly", "Calendar", "Call/Put Spread", "Risk Reversal", "Straddle/Strangle", "Straddle", "Strangle", "Call Spread", "Put Spread"];
    for (const product of products) {
      for (const w of PILLARS) {
        const legs = buildLegs(product, "BUY", "3M", "6M", LIVE_ATM, 10, w, "call", "ATM", "25Δ", live);
        expect(legs.length).toBeGreaterThan(0);
        for (const leg of legs) {
          // the strike is one of this tenor's live pillar strikes ...
          const col = ladderColumn(leg.strike, leg.tenor, PILLARS, liveStrike);
          expect(liveStrike(leg.tenor, PILLARS[col]!)).toBeCloseTo(leg.strike, 6);
          // ... and on the live level (within the smile's own wing span), not the mock one
          expect(Math.abs(leg.strike - LIVE_ATM)).toBeLessThan(0.04);
        }
      }
    }
  });
});
