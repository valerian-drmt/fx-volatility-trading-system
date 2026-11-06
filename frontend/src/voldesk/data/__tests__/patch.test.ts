/**
 * #367 (T3): push messages patch the REST slices in place. A message touches
 * only the row it names (other rows keep their object identity), a closed id
 * leaves, a no-op message returns the same array, and a stale spot message
 * never overwrites a newer row.
 */
import { describe, expect, it } from "vitest";
import type { SpotOrderRow } from "../../../api/endpoints";
import type { AccountSnapshotMsg, PositionsChangedMsg, SpotOrderChangedMsg } from "../../../hooks/streams";
import { patchAccountRaw, patchPositions, patchSpotOrderRows } from "../live/patch";
import { adaptPositions } from "../live/trade";

const NOW = Date.parse("2026-11-06T12:00:00Z");

function row(id: number, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id, package_id: `P${id}`, trade_id: `T${id}`, contract_id: 1000 + id, product_label: "Straddle",
    structure: `EUUZ6 C11${id}0`, side: "BUY", quantity: 10, tenor: "3M", tenor_now: "1M", expiry: "2026-12-04",
    current_pnl_usd: 100, market_price: 0.0131, contract_price_entry: 0.0123, nominal_eur: 1_250_000,
    delta_usd: 1, gamma_usd: 2, vega_usd: 3, theta_usd: -4, iv: 0.06, vanna_usd: 5, volga_usd: 6,
    timestamp: "2026-11-06T11:59:00Z", entry_timestamp: "2026-11-05T09:30:00Z", ...over,
  };
}

function msg(rows: Record<string, unknown>[], closed: number[] = []): PositionsChangedMsg {
  return { event: "positions_changed", source: "execution_engine", rows, closed: closed.map((id) => ({ id })),
    n_open: rows.length, timestamp: "2026-11-06T12:00:01Z" };
}

describe("patchPositions", () => {
  const base = adaptPositions([row(1), row(2), row(3)], NOW);

  it("replaces only the matching row and keeps the others by identity", () => {
    const out = patchPositions(base, msg([row(2, { quantity: 4, side: "SELL" })]), NOW);
    expect(out).not.toBe(base);
    expect(out[0]).toBe(base[0]);
    expect(out[2]).toBe(base[2]);
    expect(out[1]).toMatchObject({ id: "2", qty: 4, side: "SELL" });
  });

  it("appends a new row and drops a closed id", () => {
    const out = patchPositions(base, msg([row(9)], [1]), NOW);
    expect(out.map((p) => p.id)).toEqual(["2", "3", "9"]);
    expect(out[0]).toBe(base[1]);
  });

  it("returns the same array when nothing changes (no re-render per tick)", () => {
    expect(patchPositions(base, msg([row(2)]), NOW)).toBe(base);
    expect(patchPositions(base, msg([], []), NOW)).toBe(base);
    expect(patchPositions(base, msg([], [42]), NOW)).toBe(base);
  });
});

function spot(id: number, over: Partial<SpotOrderRow> = {}): SpotOrderRow {
  return {
    id, created_at: `2026-11-06T12:00:0${id}Z`, symbol: "EUR", currency: "USD", local_symbol: "EUR.USD",
    side: "BUY", qty: 25_000, qty_filled: 0, order_type: "LMT", limit_price: 1.1226, avg_fill_price: null,
    state: "submitted", state_updated_at: `2026-11-06T12:00:0${id}Z`, rejection_text: null, ib_order_id: 1400 + id,
    ib_status: "PreSubmitted", why_held: null, ...over,
  };
}

function spotMsg(r: SpotOrderRow): SpotOrderChangedMsg {
  return { event: "spot_order_changed", source: "execution_engine", row: r, timestamp: r.state_updated_at ?? "" };
}

describe("patchSpotOrderRows", () => {
  const base = [spot(3), spot(2), spot(1)];

  it("replaces the matching row in place", () => {
    const out = patchSpotOrderRows(base, spotMsg(spot(2, { state: "filled", qty_filled: 25_000, state_updated_at: "2026-11-06T12:00:09Z" })));
    expect(out).not.toBe(base);
    expect(out!.map((r) => r.id)).toEqual([3, 2, 1]);
    expect(out![1]).toMatchObject({ state: "filled", qty_filled: 25_000 });
    expect(out![0]).toBe(base[0]);
  });

  it("inserts a new row newest first, and from an empty slice", () => {
    expect(patchSpotOrderRows(base, spotMsg(spot(7)))!.map((r) => r.id)).toEqual([7, 3, 2, 1]);
    expect(patchSpotOrderRows(null, spotMsg(spot(7)))!.map((r) => r.id)).toEqual([7]);
  });

  it("ignores an identical or older message", () => {
    expect(patchSpotOrderRows(base, spotMsg(spot(2)))).toBe(base);
    const older = spot(2, { state: "pending", state_updated_at: "2026-11-06T11:00:00Z" });
    expect(patchSpotOrderRows(base, spotMsg(older))).toBe(base);
  });
});

describe("patchAccountRaw", () => {
  const raw = { latest: { timestamp: "2026-11-06T12:00:00Z", net_liq_usd: 100 }, prev_24h: { net_liq_usd: 90 }, buying_power_usd: 5 };
  const newer: AccountSnapshotMsg = { event: "account_snapshot", source: "execution_engine",
    latest: { timestamp: "2026-11-06T12:00:05Z", net_liq_usd: 101 }, timestamp: "2026-11-06T12:00:05Z" };

  it("swaps latest and keeps the rest of the payload", () => {
    expect(patchAccountRaw(raw, newer)).toEqual({ ...raw, latest: newer.latest });
  });

  it("ignores a message older than the held snapshot", () => {
    const older = { ...newer, latest: { timestamp: "2026-11-06T11:00:00Z", net_liq_usd: 1 } };
    expect(patchAccountRaw(raw, older)).toBe(raw);
  });
});
