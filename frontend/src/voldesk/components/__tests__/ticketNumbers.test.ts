// #479: with a preview id on screen, no number the server prices comes from
// the browser's model.
import { describe, expect, it } from "vitest";

import type { TradePreview } from "../../../api/endpoints";
import { COMMISSION_USD, serverLegs, ticketNumbers, type TicketInput } from "../ticketNumbers";

// A model that disagrees with the server on purpose, by the factors seen on
// prod on 2026-11-13 (28 times on delta, 35 times on vega).
const MODEL = { d: 280_000, g: 9_999, v: 35_000, t: -7_777, vn: 12, vg: 34, cost: 99_999 };
const BOOK = { netDelta: 1_000, netGamma: 20, netVega: 300, netTheta: -40, netVanna: 5, netVolga: 6 };

const input = (server: TradePreview | null, over: Partial<TicketInput> = {}): TicketInput => ({
  server, net: MODEL, book: BOOK, hedgeOn: false, deltaPerFuture: 145_000,
  nLegs: 1, qty: 22, isFuture: false, futureSize: "full", ...over,
});

const row = (key: string, trade: number, before: number) =>
  ({ key, label: key, unit: "", trade, before, after: before + trade, cap: null, used: null, breached: false, hard: false });

const PREVIEW: TradePreview = {
  preview_id: "tp_test",
  state: "valid_for_submit",
  pricing: { premium_paid_usd: 16_925.12, premium_per_unit_usd: 769.32 },
  costs: { commission_usd: 44, hedge_commission_usd: 7.2, total_trade_cost_usd: 16_976.32 },
  greeks_net: { delta_unhedged: -10_000, theta_usd_per_day: -120 },
  risk_budget: { failures: [], rows: [row("delta", -10_000, 1_000), row("gamma", 50, 20), row("vega", 1_000, 300)] },
  hedge: { side: "BUY", qty: 3, residual_delta_usd: -400, structure_delta_usd: -10_000 },
  structure: {
    legs: [{ contract_type: "put", side: "BUY", tenor: "3M", qty: 22, strike: 1.1, entry_iv_pct: 6.2, entry_price_per_contract_usd: 769.32 }],
  },
} as unknown as TradePreview;

const modelValues = new Set(Object.values(MODEL));

describe("ticketNumbers", () => {
  it("shows the server's numbers once a preview exists, never the model's", () => {
    const t = ticketNumbers(input(PREVIEW));
    expect(t.source).toBe("server");
    expect(t.premiumAbs).toBe(16_925.12);
    expect(t.commission).toBe(44);
    expect(t.netCash).toBeCloseTo(-16_969.12, 2);
    const priced = t.rows.filter((r) => r.source === "server");
    expect(priced.map((r) => r.name)).toEqual(["Delta", "Gamma", "Vega", "Theta"]);
    for (const r of priced) expect(modelValues.has(r.val as number)).toBe(false);
    expect(priced.find((r) => r.name === "Vega")).toMatchObject({ val: 1_000, before: 300, after: 1_300 });
    expect(priced.find((r) => r.name === "Theta")).toMatchObject({ val: -120, before: -40, after: -160 });
  });

  it("labels the two greeks the server does not send as estimates", () => {
    const est = ticketNumbers(input(PREVIEW)).rows.filter((r) => r.source === "estimate");
    expect(est.map((r) => r.name)).toEqual(["Vanna (est.)", "Volga (est.)"]);
  });

  it("shows a missing server number as missing, not as the model's", () => {
    // the gate could not be asked: no budget rows, no commission
    const bare = { preview_id: "tp_bare", state: "blocked" } as TradePreview;
    const t = ticketNumbers(input(bare));
    expect(t.premiumAbs).toBeNull();
    expect(t.commission).toBeNull();
    expect(t.netCash).toBeNull();
    for (const r of t.rows.filter((x) => x.source === "server" && x.name !== "Theta")) {
      expect(r.val).toBeNull();
      expect(r.after).toBeNull();
    }
  });

  it("shows both deltas of a hedged package, and the hedge's commission, from the server", () => {
    const t = ticketNumbers(input(PREVIEW, { hedgeOn: true }));
    expect(t.hedged).toBe(true);
    expect([t.hedgeSide, t.hedgeQty]).toEqual(["BUY", 3]);
    expect(t.commission).toBeCloseTo(51.2, 6);
    expect(t.rows[0]).toMatchObject({ name: "Delta", val: -10_000, before: 1_000, after: -9_000 });
    expect(t.rows[1]).toMatchObject({ name: "Delta after hedge", val: -10_000 });
  });

  it("is a labelled sketch before a preview exists, at the server's commission rates", () => {
    const t = ticketNumbers(input(null, { hedgeOn: true }));
    expect(t.source).toBe("estimate");
    expect(t.rows.every((r) => r.source === "estimate")).toBe(true);
    // 22 options at the option rate, 2 futures of hedge (280k / 145k) at the 6E rate
    expect(t.hedgeQty).toBe(2);
    expect(t.commission).toBeCloseTo(22 * COMMISSION_USD.option + 2 * COMMISSION_USD.full, 6);
  });

  it("reads the legs table from the server: strike, vol, premium at the size", () => {
    expect(serverLegs(PREVIEW)).toEqual([
      { label: "22× Put 3M", side: "BUY", strike: 1.1, ivPct: 6.2, premium: -(22 * 769.32) },
    ]);
  });
});

describe("OrderBuilder", () => {
  it("reads no greek, premium or cost from the browser model directly", async () => {
    // Every number goes through ticketNumbers, which decides its source. A
    // line like `net.v` in the ticket is how a browser greek got beside a
    // preview id.
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const src = readFileSync(resolve(process.cwd(), "src/voldesk/components/OrderBuilder.tsx"), "utf-8");
    expect(src.match(/\bnet\.(d|g|v|t|vn|vg|cost)\b/g)).toBeNull();
    expect(src).not.toMatch(/\* 2\.1\b/);
  });
});
