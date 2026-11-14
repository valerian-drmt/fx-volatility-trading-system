// Where each number on the order ticket comes from (#479).
//
// Before a preview exists the ticket is a sketch: the browser's model prices
// the legs so the trader sees an order of magnitude while composing.
//
// Once a preview exists (a preview id is on screen) every number that the
// server prices comes from the server, or is shown as missing. The browser's
// model is never mixed in beside a preview id: on 2026-11-13 the two differed
// by 28 times on delta and 35 times on vega, and a fallback to the model when
// a server number was absent is how the wrong one got on screen. Vanna and
// volga are the exception: the server does not send them, they stay the
// model's and are labelled "(est.)".
import type { TradePreview } from "../../api/endpoints";

// The server's table (core.trade_preview.COMMISSION_USD), per contract and per
// side. Used for the sketch only; a test keeps the two files equal.
export const COMMISSION_USD = { option: 2.0, full: 2.4, micro: 0.6 } as const;

export type Source = "server" | "estimate";

export interface ModelNet { d: number; g: number; v: number; t: number; vn: number; vg: number; cost: number }
export interface BookGreeks {
  netDelta: number; netGamma: number; netVega: number; netTheta: number; netVanna: number; netVolga: number;
}

export interface ImpactRow {
  name: string;
  unit: string;
  val: number | null;
  before: number | null;
  after: number | null;
  fmt: "usd" | "k";
  source: Source;
}

export interface TicketInput {
  server: TradePreview | null;
  net: ModelNet;
  book: BookGreeks;
  hedgeOn: boolean;
  /** USD delta of one 6E future at the current spot: sizes the sketch's hedge. */
  deltaPerFuture: number;
  nLegs: number;
  qty: number;
  isFuture: boolean;
  futureSize: "full" | "micro";
}

export interface TicketNumbers {
  source: Source;
  premiumAbs: number | null;
  isCredit: boolean;
  commission: number | null;      // options and futures of the structure, and its hedge
  netCash: number | null;
  hedgeQty: number;
  hedgeSide: "BUY" | "SELL";
  hedged: boolean;
  structDelta: number | null;
  rows: ImpactRow[];
}

const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);
const plus = (a: number | null, b: number | null): number | null => (a == null || b == null ? null : a + b);

/** The server's row for one greek of the book impact, or nulls. */
function budgetRow(server: TradePreview, key: string): { val: number | null; before: number | null; after: number | null } {
  const r = server.risk_budget?.rows.find((x) => x.key === key);
  return { val: num(r?.trade), before: num(r?.before), after: num(r?.after) };
}

export function ticketNumbers(i: TicketInput): TicketNumbers {
  const { server, net, book } = i;
  if (!server) return sketch(i);

  const premium = num(server.pricing?.premium_paid_usd);
  const comm = num(server.costs?.commission_usd);
  const hedge = i.hedgeOn ? server.hedge ?? null : null;
  const hedgeComm = hedge ? num(server.costs?.hedge_commission_usd) : 0;
  const commission = plus(comm, hedgeComm);
  const isCredit = premium != null && premium < 0;
  const premiumAbs = premium == null ? null : Math.abs(premium);
  const netCash = premiumAbs == null || commission == null ? null : (isCredit ? premiumAbs : -premiumAbs) - commission;

  const hedged = !!hedge && hedge.qty > 0;
  const structDelta = hedge ? num(hedge.structure_delta_usd) : num(server.greeks_net?.delta_unhedged);
  const d = budgetRow(server, "delta"), g = budgetRow(server, "gamma"), v = budgetRow(server, "vega");
  const theta = num(server.greeks_net?.theta_usd_per_day);
  const S: Source = "server";
  const deltaRows: ImpactRow[] = hedged
    ? [
      { name: "Delta", unit: "USD", val: structDelta, before: d.before, after: plus(d.before, structDelta), fmt: "usd", source: S },
      { name: "Delta after hedge", unit: "USD", ...d, fmt: "usd", source: S },
    ]
    : [{ name: "Delta", unit: "USD", ...d, fmt: "usd", source: S }];
  const rows: ImpactRow[] = [
    ...deltaRows,
    { name: "Gamma", unit: "USD/pip", ...g, fmt: "usd", source: S },
    { name: "Vega", unit: "$/vp", ...v, fmt: "usd", source: S },
    // theta is not a limit, so the gate has no row for it: the book's own figure
    { name: "Theta", unit: "$/day", val: theta, before: book.netTheta, after: plus(book.netTheta, theta), fmt: "usd", source: S },
    { name: "Vanna (est.)", unit: "$k/vp·fig", val: net.vn, before: book.netVanna, after: book.netVanna + net.vn, fmt: "k", source: "estimate" },
    { name: "Volga (est.)", unit: "$k/vp", val: net.vg, before: book.netVolga, after: book.netVolga + net.vg, fmt: "k", source: "estimate" },
  ];
  return {
    source: S, premiumAbs, isCredit, commission, netCash,
    hedgeQty: hedge?.qty ?? 0,
    hedgeSide: (hedge?.side === "BUY" ? "BUY" : "SELL"),
    hedged, structDelta, rows,
  };
}

function sketch(i: TicketInput): TicketNumbers {
  const { net, book } = i;
  const E: Source = "estimate";
  const rate = i.isFuture ? COMMISSION_USD[i.futureSize] : COMMISSION_USD.option;
  const hedgeQty = i.hedgeOn && i.deltaPerFuture > 0 ? Math.round(Math.abs(net.d) / i.deltaPerFuture) : 0;
  const hedged = i.hedgeOn && hedgeQty > 0;
  const residual = net.d - Math.sign(net.d) * hedgeQty * i.deltaPerFuture;
  const commission = i.nLegs * i.qty * rate + hedgeQty * COMMISSION_USD.full;
  const isCredit = net.cost < 0;
  const premiumAbs = Math.abs(net.cost);
  const row = (name: string, unit: string, val: number, before: number, fmt: "usd" | "k"): ImpactRow =>
    ({ name, unit, val, before, after: before + val, fmt, source: E });
  const deltaRows = hedged
    ? [row("Delta", "USD", net.d, book.netDelta, "usd"), row("Delta after hedge", "USD", residual, book.netDelta, "usd")]
    : [row("Delta", "USD", net.d, book.netDelta, "usd")];
  return {
    source: E, premiumAbs, isCredit, commission,
    netCash: (isCredit ? premiumAbs : -premiumAbs) - commission,
    hedgeQty, hedgeSide: net.d > 0 ? "SELL" : "BUY", hedged, structDelta: net.d,
    rows: [
      ...deltaRows,
      row("Gamma", "USD/pip", net.g, book.netGamma, "usd"),
      row("Vega", "$/vp", net.v, book.netVega, "usd"),
      row("Theta", "$/day", net.t, book.netTheta, "usd"),
      row("Vanna", "$k/vp·fig", net.vn, book.netVanna, "k"),
      row("Volga", "$k/vp", net.vg, book.netVolga, "k"),
    ],
  };
}

export interface TicketLeg {
  label: string;
  side: string;
  strike: number | null;
  ivPct: number | null;
  /** Premium of the leg at the size traded, signed like cash: negative = paid. */
  premium: number | null;
}

/** The legs table once a preview exists: the server's strikes, vols and prices. */
export function serverLegs(server: TradePreview): TicketLeg[] {
  return (server.structure?.legs ?? []).map((l) => {
    const price = num(l.entry_price_per_contract_usd);
    const qty = num(l.qty) ?? 0;
    const isFut = l.contract_type === "future";
    const kind = isFut ? "Future" : l.contract_type === "call" ? "Call" : "Put";
    return {
      label: `${qty}× ${kind} ${l.tenor ?? ""}`.trim(),
      side: l.side ?? "",
      strike: isFut ? null : num(l.strike),
      ivPct: isFut ? null : num(l.entry_iv_pct),
      premium: isFut || price == null ? null : (l.side === "BUY" ? -1 : 1) * price * qty,
    };
  });
}
