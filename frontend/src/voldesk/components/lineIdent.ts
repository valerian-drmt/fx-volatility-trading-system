/**
 * VOLDESK — the identity of a position line (data side): its type, the
 * adapter from the server payload, the fallback for a row that came without
 * one, and the single grouping / ordering rule of the three position panels.
 * The cells that draw it are in LineIdentity.tsx.
 */
import { PRODUCT_NAMES, formatStructLabel } from "./tradeGrouping";

export interface LineIdent {
  tradeId: string | null;
  contractId: number | null;
  symbol: string;
  tradeName: string; // what the trade is: "Butterfly", "Strangle 10Δ", "Future"
  legName: string; // what the contract is: "Vanilla Call", "Future - 6E"
  side: string;
  qty: number;
  tenor: string;
  legsOpen: number;
  legsTotal: number;
  /** The close in flight on this line, when there is one. */
  closing?: LineClosing | undefined;
}

/** A close order on its way: `sending` (not at IB yet), `sent` (placed, not
 * confirmed), `waiting` (IB confirmed it holds the order and it waits for a
 * counterparty), `partial` (partly filled). Never "rejected": a rejection is
 * IB refusing the order, and then there is no close in flight. */
export interface LineClosing {
  phase: "sending" | "sent" | "waiting" | "partial";
  qty: number;
  limit: number | null;
  orders: number;
  structureIds: number[];
}

/** The desk's words for a working order state, in Open positions and Orders. */
export const CLOSE_PHASE_LABEL: Record<LineClosing["phase"], string> = {
  sending: "closing · sending",
  sent: "closing · sent to IB",
  waiting: "closing · waiting at IB",
  partial: "closing · partly filled",
};

function adaptClosing(raw: unknown): LineClosing | undefined {
  if (raw == null || typeof raw !== "object") return undefined;
  const r = raw as {
    phase?: string; qty?: number; limit_price?: number | null; n_orders?: number; structure_ids?: number[];
  };
  const phase = r.phase === "sending" || r.phase === "waiting" || r.phase === "partial" ? r.phase : "sent";
  return {
    phase, qty: r.qty ?? 0, limit: r.limit_price ?? null, orders: r.n_orders ?? 1,
    structureIds: Array.isArray(r.structure_ids) ? r.structure_ids : [],
  };
}

interface RawIdentity {
  trade_id?: number | string | null;
  contract_id?: number | null;
  symbol?: string | null;
  structure_type?: string | null;
  structure_label?: string | null;
  leg_label?: string | null;
  side?: string | null;
  qty?: number | null;
  tenor?: string | null;
  legs_open?: number | null;
  legs_total?: number | null;
  closing?: unknown;
}

/** The trade's name from its booked type, the same rule on every panel. */
export function tradeNameOf(structureType: string | null | undefined, label: string | null | undefined, fallback: string): string {
  const st = (structureType ?? "").toLowerCase();
  return PRODUCT_NAMES[st] ?? formatStructLabel(label) ?? formatStructLabel(structureType) ?? fallback;
}

/** Server identity -> the shape the cells draw. undefined when absent. */
export function adaptIdent(raw: unknown): LineIdent | undefined {
  if (raw == null || typeof raw !== "object") return undefined;
  const r = raw as RawIdentity;
  const legName = r.leg_label ?? "—";
  return {
    tradeId: r.trade_id == null ? null : String(r.trade_id),
    contractId: r.contract_id ?? null,
    symbol: r.symbol ?? "—",
    tradeName: tradeNameOf(r.structure_type, r.structure_label, legName),
    legName,
    side: r.side ?? "—",
    qty: typeof r.qty === "number" ? r.qty : 0,
    tenor: r.tenor ?? "—",
    legsOpen: r.legs_open ?? 1,
    legsTotal: r.legs_total ?? 1,
    closing: adaptClosing(r.closing),
  };
}

/** Identity for a row that came without one (a live push, an old payload):
 * built from the row's own fields so the columns never go blank. */
export function identFrom(p: {
  tradeId?: string | number | null; conId?: number | null; product?: string; structure?: string;
  side?: string; qty?: number; tenor?: string;
}): LineIdent {
  const name = p.product || "—";
  return {
    tradeId: p.tradeId == null || p.tradeId === "" ? null : String(p.tradeId),
    contractId: p.conId || null,
    symbol: p.structure || "—",
    tradeName: name, legName: name,
    side: p.side ?? "—", qty: p.qty ?? 0, tenor: p.tenor || "—",
    legsOpen: 1, legsTotal: 1,
  };
}

interface Identified {
  ident?: LineIdent | undefined;
  lineId?: string | undefined;
}

/** Rows grouped by trade, in ONE order for the three panels: newest trade
 * first, lines of a trade by symbol, lines without a trade last. */
export function groupLines<T extends Identified>(rows: T[]): { key: string; tradeId: string | null; legs: T[] }[] {
  const num = (r: T): number => (r.ident?.tradeId != null ? Number(r.ident.tradeId) : -1);
  const sorted = [...rows].sort(
    (a, b) => num(b) - num(a) || (a.ident?.symbol ?? "").localeCompare(b.ident?.symbol ?? ""),
  );
  const groups: { key: string; tradeId: string | null; legs: T[] }[] = [];
  const idx: Record<string, number> = {};
  for (const r of sorted) {
    const tid = r.ident?.tradeId ?? null;
    const key = tid != null ? "T" + tid : "L" + (r.lineId ?? r.ident?.symbol ?? groups.length);
    if (idx[key] === undefined) {
      idx[key] = groups.length;
      groups.push({ key, tradeId: tid, legs: [] });
    }
    groups[idx[key]]!.legs.push(r);
  }
  return groups;
}
