/**
 * Spot EUR/USD cash ticket helpers (#290 / #291).
 *
 *  - `spotLimitPrice` : the marketable limit the ticket sends instead of a market
 *    order. IDEALPRO simulates market orders (market-with-protection) and, on the
 *    paper account, parks them in PreSubmitted at the then-bid; a limit a couple
 *    of pips through the touch is a real `Submitted` order that fills on the touch.
 *  - `adaptSpotOrders` : /trade/spot-orders rows -> the Orders blotter's row shape.
 */
import type { SpotOrderRow } from "../../../api/endpoints";

export const SPOT_PIP = 0.0001;
/** IDEALPRO minimum price variation for EUR.USD. A limit off this grid is
 * refused by IB (error 110) and used to show up as a silent "cancelled" (#376). */
export const SPOT_TICK = 0.00005;
/** Pips through the touch — enough to cross a normal IDEALPRO spread, small
 * enough that a fill is still "at market" for cash management. */
export const SPOT_LIMIT_PIPS = 2;

/** Snap onto the IDEALPRO tick toward the aggressive side: SELL rounds down,
 * BUY rounds up, so a marketable limit stays marketable. */
export function snapSpotLimit(price: number, side: "BUY" | "SELL"): number {
  const steps = price / SPOT_TICK;
  const n = side === "SELL" ? Math.floor(steps + 1e-9) : Math.ceil(steps - 1e-9);
  return Math.round(n * SPOT_TICK * 1e5) / 1e5;
}

/** SELL (buy USD / sell EUR) crosses the bid ; BUY (buy EUR) crosses the ask,
 * then snaps onto the 0.00005 tick. Null when the quote isn't usable. */
export function spotLimitPrice(side: "BUY" | "SELL", bid: number, ask: number): number | null {
  if (!Number.isFinite(bid) || !Number.isFinite(ask) || bid <= 0 || ask <= 0 || ask < bid) return null;
  const raw = side === "SELL" ? bid - SPOT_LIMIT_PIPS * SPOT_PIP : ask + SPOT_LIMIT_PIPS * SPOT_PIP;
  return snapSpotLimit(raw, side);
}

export interface SpotBlotterRow {
  id: string; // "spot<id>"
  spotId: number;
  tradeTag: string; // "S<id>" — spot orders have no trade_structure #
  ts: Date;
  contract: string; // "EUR.USD"
  label: string; // "Spot · Buy EUR" / "Spot · Buy USD"
  side: string;
  qty: number; // EUR notional
  qtyFilled: number;
  limitPrice: number | null;
  avgFillPrice: number | null;
  state: string;
  note: string | null; // "@ 1.12325 lmt · PreSubmitted" / rejection text
  ibOrderId: number | null;
  ibStatus: string | null; // IB's own status string while the order works (#376)
}

export function spotOrderLabel(side: string): string {
  // IB quantifies EUR.USD in EUR for both directions : BUY = buy EUR / sell USD.
  return side === "SELL" ? "Spot · Buy USD" : "Spot · Buy EUR";
}

export function adaptSpotOrders(raw: unknown): SpotBlotterRow[] {
  const rows = Array.isArray(raw) ? (raw as SpotOrderRow[]) : [];
  return rows.map((r) => {
    // While the order works, say where IB holds it ("PreSubmitted" = parked,
    // "Submitted" = live on the book) and IB's reason when it gives one, so a
    // waiting row is diagnosable from the blotter alone (#376).
    const working = r.state === "pending" || r.state === "submitted" || r.state === "partially_filled";
    const ibHint = working && r.ib_status
      ? ` · ${r.ib_status}${r.why_held ? ` (${r.why_held})` : ""}`
      : "";
    const note =
      r.state === "rejected" && r.rejection_text
        ? r.rejection_text
        : r.avg_fill_price != null && r.qty_filled > 0
          ? `avg ${r.avg_fill_price.toFixed(5)}`
          : r.limit_price != null
            ? `@ ${r.limit_price.toFixed(5)} lmt${ibHint}`
            : r.order_type === "MKT"
              ? `@ mkt${ibHint}`
              : null;
    return {
      id: "spot" + r.id,
      spotId: r.id,
      tradeTag: "S" + r.id,
      ts: new Date(r.created_at),
      contract: r.local_symbol || `${r.symbol}.${r.currency}`,
      label: spotOrderLabel(r.side),
      side: r.side,
      qty: r.qty,
      qtyFilled: r.qty_filled ?? 0,
      limitPrice: r.limit_price,
      avgFillPrice: r.avg_fill_price,
      state: r.state,
      note,
      ibOrderId: r.ib_order_id,
      ibStatus: r.ib_status ?? null,
    };
  });
}
