import { describe, expect, it } from "vitest";
import { adaptSpotOrders, snapSpotLimit, spotLimitPrice, spotOrderLabel } from "../live/spot";

describe("spot ticket — marketable limit (#290)", () => {
  it("SELL crosses the bid by 2 pips, BUY crosses the ask by 2 pips, on the 0.00005 tick", () => {
    expect(spotLimitPrice("SELL", 1.12345, 1.12347)).toBe(1.12325);
    // 1.12347 + 2 pips = 1.12367 is off IDEALPRO's tick -> rounds UP to 1.12370 (BUY stays marketable)
    expect(spotLimitPrice("BUY", 1.12345, 1.12347)).toBe(1.1237);
  });
  it("refuses an unusable quote (missing, non-positive, crossed)", () => {
    expect(spotLimitPrice("SELL", NaN, 1.1)).toBeNull();
    expect(spotLimitPrice("BUY", 0, 1.1)).toBeNull();
    expect(spotLimitPrice("BUY", 1.2, 1.1)).toBeNull();
  });
});

describe("spot orders → blotter rows (#291)", () => {
  it("maps a working limit row with its IB cancel handle and the limit note", () => {
    const rows = adaptSpotOrders([{
      id: 7, created_at: "2026-11-05T10:05:00Z", symbol: "EUR", currency: "USD", local_symbol: "EUR.USD",
      side: "SELL", qty: 44513, qty_filled: 0, order_type: "LMT", limit_price: 1.12325,
      avg_fill_price: null, state: "submitted", rejection_text: null, ib_order_id: 5,
    }]);
    expect(rows).toHaveLength(1);
    const r = rows[0]!;
    expect(r.tradeTag).toBe("S7");
    expect(r.contract).toBe("EUR.USD");
    expect(r.label).toBe("Spot · Buy USD");
    expect(r.qty).toBe(44513);
    expect(r.state).toBe("submitted");
    expect(r.note).toBe("@ 1.12325 lmt");
    expect(r.ibOrderId).toBe(5);
  });
  it("a filled row notes its average price; a rejected row its reason", () => {
    const [filled, rejected] = adaptSpotOrders([
      { id: 1, created_at: "2026-11-05T10:05:00Z", symbol: "EUR", currency: "USD", local_symbol: "EUR.USD",
        side: "BUY", qty: 100000, qty_filled: 100000, order_type: "LMT", limit_price: 1.1237,
        avg_fill_price: 1.12348, state: "filled", rejection_text: null, ib_order_id: 6 },
      { id: 2, created_at: "2026-11-05T10:06:00Z", symbol: "EUR", currency: "USD", local_symbol: "",
        side: "BUY", qty: 100000, qty_filled: 0, order_type: "MKT", limit_price: null,
        avg_fill_price: null, state: "rejected", rejection_text: "Inactive", ib_order_id: null },
    ]);
    expect(filled!.label).toBe("Spot · Buy EUR");
    expect(filled!.note).toBe("avg 1.12348");
    expect(rejected!.contract).toBe("EUR.USD"); // falls back to symbol.currency
    expect(rejected!.note).toBe("Inactive");
    expect(rejected!.ibOrderId).toBeNull();
  });
  it("non-array payloads yield no rows", () => {
    expect(adaptSpotOrders(null)).toEqual([]);
    expect(adaptSpotOrders({})).toEqual([]);
    expect(spotOrderLabel("SELL")).toBe("Spot · Buy USD");
  });
});

describe("spot ticket — IDEALPRO tick + IB status (#376)", () => {
  it("snaps the limit onto the 0.00005 tick toward the aggressive side", () => {
    // prod row S3: bid 1.12278 - 2 pips = 1.12258, off-grid -> IB error 110
    expect(snapSpotLimit(1.12258, "SELL")).toBe(1.12255);
    expect(snapSpotLimit(1.12258, "BUY")).toBe(1.1226);
    expect(spotLimitPrice("SELL", 1.12278, 1.1228)).toBe(1.12255);
    expect(spotLimitPrice("BUY", 1.12278, 1.12282)).toBe(1.12305);
  });
  it("a working row says where IB holds it; a refused row shows IB's reason", () => {
    const [held, live, refused] = adaptSpotOrders([
      { id: 1, created_at: "2026-11-05T10:45:28Z", symbol: "EUR", currency: "USD", local_symbol: "EUR.USD",
        side: "SELL", qty: 44513, qty_filled: 0, order_type: "LMT", limit_price: 1.12335, avg_fill_price: null,
        state: "submitted", rejection_text: null, ib_order_id: 7, ib_status: "PreSubmitted", why_held: "locate" },
      { id: 2, created_at: "2026-11-05T10:45:28Z", symbol: "EUR", currency: "USD", local_symbol: "EUR.USD",
        side: "SELL", qty: 44513, qty_filled: 0, order_type: "LMT", limit_price: 1.12325, avg_fill_price: null,
        state: "submitted", rejection_text: null, ib_order_id: 5, ib_status: "Submitted", why_held: null },
      { id: 3, created_at: "2026-11-05T10:47:59Z", symbol: "EUR", currency: "USD", local_symbol: "EUR.USD",
        side: "SELL", qty: 44532, qty_filled: 0, order_type: "LMT", limit_price: 1.12258, avg_fill_price: null,
        state: "rejected", rejection_text: "Error 110, reqId 12: The price does not conform to the minimum price variation for this contract.",
        ib_order_id: 12, ib_status: "Cancelled", why_held: null },
    ]);
    expect(held!.note).toBe("@ 1.12335 lmt · PreSubmitted (locate)");
    expect(held!.ibStatus).toBe("PreSubmitted");
    expect(live!.note).toBe("@ 1.12325 lmt · Submitted");
    expect(refused!.note).toContain("minimum price variation");
  });
});
