import { describe, expect, it } from "vitest";
import { adaptSpotOrders, spotLimitPrice, spotOrderLabel } from "../live/spot";

describe("spot ticket — marketable limit (#290)", () => {
  it("SELL crosses the bid by 2 pips, BUY crosses the ask by 2 pips, 5-dp tick", () => {
    expect(spotLimitPrice("SELL", 1.12345, 1.12347)).toBe(1.12325);
    expect(spotLimitPrice("BUY", 1.12345, 1.12347)).toBe(1.12367);
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
