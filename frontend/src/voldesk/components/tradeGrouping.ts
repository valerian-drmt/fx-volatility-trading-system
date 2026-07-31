/**
 * VOLDESK — shared trade-grouping helpers: group per-leg rows by trade and infer
 * a structure name / side. Used by Open positions (Trade), Position breakdown
 * (Risk), and the by-trade P&L-attribution matrix (Portfolio) so all three read
 * the flat per-leg feed as one summary line per trade with its legs underneath.
 */
import { DATA } from "../data";

// Minimal shape the structure-name / grouping helpers need — different row types
// (Position, PositionAttribRow) all satisfy it.
export interface LegLike {
  id: string | number;
  product: string;
  structure: string;
  side: string;
  strike?: number | null;
  tenor?: string;
}

/** Group any per-leg rows by trade (rows without a trade_id stay singletons). */
export function groupByTradeId<T extends { id: string | number; tradeId?: string | number | null }>(
  rows: T[],
): { key: string; tradeId: string | null; legs: T[] }[] {
  const groups: { key: string; tradeId: string | null; legs: T[] }[] = [];
  const idx: Record<string, number> = {};
  for (const p of rows) {
    const key = p.tradeId != null ? "T" + p.tradeId : "S" + p.id;
    if (idx[key] === undefined) {
      idx[key] = groups.length;
      groups.push({ key, tradeId: p.tradeId != null ? String(p.tradeId) : null, legs: [] });
    }
    groups[idx[key]]!.legs.push(p);
  }
  return groups;
}

// Strike for a leg : from the explicit field (mock) or parsed from the IB local
// symbol ("EUUQ6 C1145" → 1.1450).
export function legStrikeNum(p: LegLike): number | null {
  if (p.strike && p.strike > 0) return p.strike;
  const m = /\s[CP](\d{3,5})$/.exec(p.structure || "");
  return m ? parseInt(m[1]!, 10) / 1000 : null;
}

function legLevel(p: LegLike): string | null {
  return DATA.strikeToWing(legStrikeNum(p), p.tenor ?? "");
}
const rank = (lvl: string): number => (lvl === "ATM" ? 50 : parseInt(lvl, 10));

// Wing derivation — bucket each leg's strike into a smile delta pillar so the
// vertical structures read like "Call Spread ATM/10Δ" / "Risk Reversal 25Δ".
function deriveWing(legs: LegLike[], base: string): string | null {
  const levels = legs.map(legLevel).filter((x): x is string => x != null);
  if (levels.length < 2) return null;
  if (/Strangle|Risk Reversal/.test(base)) return levels[0]!; // symmetric → one Δ
  const uniq = [...new Set(levels)].sort((a, b) => rank(b) - rank(a));
  return uniq.length >= 2 ? `${uniq[0]}/${uniq[1]}` : (uniq[0] ?? null);
}

// Structure name for the summary line. Prefer a shared explicit label; otherwise
// infer it from the legs (product + side + strike).
export function structureName(legs: LegLike[]): string {
  const base = ((): string => {
    const s0 = legs[0]!.structure;
    if (s0 && s0 !== "—" && legs.every((l) => l.structure === s0)) return s0;
    if (legs.length === 1) return legs[0]!.product || s0 || "—";
    const calls = legs.filter((l) => /call/i.test(l.product));
    const puts = legs.filter((l) => /put/i.test(l.product));
    const n = legs.length;
    if (n === 2 && calls.length === 1 && puts.length === 1) {
      if (calls[0]!.side !== puts[0]!.side) return "Risk Reversal";
      return calls[0]!.strike && calls[0]!.strike === puts[0]!.strike ? "Straddle" : "Strangle";
    }
    if (n === 2 && calls.length === 2) return "Call Spread";
    if (n === 2 && puts.length === 2) return "Put Spread";
    if (n === 3) return "Butterfly";
    if (n === 4) return "Condor";
    return `Structure · ${n} legs`;
  })();
  if (!/\d+Δ|ATM/.test(base) && /^(Risk Reversal|Call Spread|Put Spread|Strangle)\b/.test(base)) {
    const wing = deriveWing(legs, base);
    if (wing) return `${base} ${wing}`;
  }
  return base;
}

// ── Authoritative structure name (from the DB classifier, never leg-inference) ──
// The booking pipeline stores each trade's structure_type + product_label; those
// are the source of truth for the summary name. structureName() (leg-inference)
// is only a fallback for rows with no trade context — it buckets live strikes
// against the smile and snaps a 25Δ strangle / a straddle / a calendar all to
// "Strangle 10Δ", which is why panels must prefer the stored label below.
export const PRODUCT_NAMES: Record<string, string> = {
  vanilla_call: "Vanilla Call", vanilla_put: "Vanilla Put",
  straddle_atm: "Straddle", straddle: "Straddle", strangle: "Strangle",
  butterfly: "Butterfly", risk_reversal: "Risk Reversal", calendar: "Calendar", future: "Future",
  "call spread": "Call Spread", "put spread": "Put Spread",
};

// Format a classifier label (stored structure_type / product_label, e.g.
// "long strangle 25d") into a clean product name. Returns null for empty /
// "custom" so callers fall through to the next source.
export function formatStructLabel(label: string | null | undefined): string | null {
  if (!label) return null;
  const l = label.toLowerCase().trim();
  if (l === "custom" || l === "") return null;
  const sm = /strangle\s*(\d+)\s*d/.exec(l);
  if (sm) return `Strangle ${sm[1]}Δ`;
  if (l.includes("strangle")) return "Strangle";
  if (l.includes("straddle")) return "Straddle";
  const rrm = /risk reversal\s*(\d+)\s*d/.exec(l);
  if (rrm) return `Risk Reversal ${rrm[1]}Δ`;
  if (l.includes("risk reversal")) return "Risk Reversal";
  if (l.includes("butterfly")) return "Butterfly";
  if (l.includes("calendar")) return "Calendar";
  if (l.includes("call spread")) return "Call Spread";
  if (l.includes("put spread")) return "Put Spread";
  if (l.includes("vertical spread")) return "Vertical Spread";
  if (l.includes("future")) return "Future";
  const bare = l.replace(/^(long|short)\s+/, "");  // vanilla single-leg
  if (bare === "call") return "Vanilla Call";
  if (bare === "put") return "Vanilla Put";
  return label.replace(/_/g, " ").trim().replace(/\b\w/g, (c) => c.toUpperCase());
}

// Minimal shape of a StructuredPositions structure row.
export interface StructRowLike {
  structure_id: string | number;
  structure_type?: string | null;
  product_label?: string | null;
}

/** trade_id → authoritative structure name, from the DB structured feed. Panels
 *  prefer this over structureName(legs); missing ids fall back to inference. */
export function structureNamesByTrade(structures: StructRowLike[]): Record<string, string> {
  const m: Record<string, string> = {};
  for (const s of structures) {
    m[String(s.structure_id)] =
      PRODUCT_NAMES[s.structure_type ?? ""]
      ?? formatStructLabel(s.product_label)
      ?? formatStructLabel(s.structure_type)
      ?? "Structure";
  }
  return m;
}

// Structure-level side (BUY/SELL) — count majority, else the first (entry) leg.
export function structureSide(legs: LegLike[]): string {
  const buys = legs.filter((l) => l.side === "BUY").length;
  const sells = legs.length - buys;
  if (buys > sells) return "BUY";
  if (sells > buys) return "SELL";
  return legs[0]?.side ?? "—";
}
