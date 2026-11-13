/**
 * VOLDESK — why an order was refused, in the desk's words (#478).
 *
 * The api explains every refusal: a code, and the numbers behind it. The
 * ticket used to keep the error name only ("pretrade gate failed"), so the
 * one move left to the trader was to try again. This module turns what the
 * server sent into sentences; it invents nothing, and an unknown code is
 * shown as it came.
 */
import { ApiError } from "../../api/client";

const usd = (v: unknown): string => {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  const a = Math.abs(n);
  const s = a >= 1e6 ? (a / 1e6).toFixed(2) + "M" : a >= 1e3 ? (a / 1e3).toFixed(1) + "k" : a.toFixed(0);
  return (n < 0 ? "-$" : "$") + s;
};
const secs = (v: unknown): string => (typeof v === "number" && Number.isFinite(v) ? Math.round(v) + " s" : "unknown");

type Dict = Record<string, unknown>;
const dict = (v: unknown): Dict => (v && typeof v === "object" ? (v as Dict) : {});

/** One sentence per gate failure code, with the gate's own numbers. */
export function explainGateFailure(code: string, details: unknown): string {
  const d = dict(details);
  const caps = dict(d.caps);
  const post = dict(d.post_trade);
  const over = (label: string, value: unknown, cap: unknown, unit: string): string =>
    `${label} after the trade ${usd(value)}${unit}, limit ${usd(cap)}${unit}`;
  switch (code) {
    case "cap_delta": return over("Delta", post.delta_usd, caps.delta_usd, "");
    case "cap_vega": return over("Vega", post.vega_usd_per_volpt, caps.vega_usd_per_volpt, " per vol point");
    case "cap_gamma": return over("Gamma", post.gamma_usd_per_pip, caps.gamma_usd_per_pip, " per pip");
    case "cap_notional": {
      const eur = (v: unknown): string => "€" + (Number(v) / 1e6).toFixed(2) + "M";
      return `Notional ${eur(post.notional_eur)}, limit ${eur(caps.max_notional_eur)}`;
    }
    case "book_partially_priced": {
      const lines = Array.isArray(d.book_unpriced_lines) ? (d.book_unpriced_lines as unknown[]).join(", ") : "";
      return `A held position has no delta, so the book cannot be checked${lines ? ` (${lines})` : ""}`;
    }
    case "trading_halted": return `Trading is halted${d.halt_reason ? `: ${String(d.halt_reason)}` : ""}`;
    case "halt_flag_unreadable": return "The kill switch could not be read";
    case "symbol_not_allowed": return "This contract is not on the allowed list";
    case "account_type_mismatch":
      return `The IB session is "${String(d.ib_account_type ?? "unknown")}", the desk is set to "${String(d.required_account_type ?? "?")}"`;
    case "risk_engine_stale": return `The risk engine has not reported for ${secs(d.risk_heartbeat_age_s)}`;
    case "surface_stale":
      return `The vol surface is ${secs(d.surface_age_s ?? d.surface_age_seconds)} old, limit ${secs(d.max_surface_age_s ?? d.max_iv_age_seconds)}`;
    case "surface_age_unknown": return "The age of the vol surface is unknown";
    case "surface_arbitrage": return "The vol surface fails the arbitrage check";
    case "caps_unavailable": return "The limits could not be computed";
    case "caps_not_established": return "The limits are not established yet (no account history or no spot)";
    case "book_unavailable": return "The book could not be read";
    case "trade_greeks_unavailable": return "The trade's greeks are missing from the preview";
    case "limits_need_confirmation": return "The trade is close to a limit and needs your confirmation";
    case "preview_expired": return "The preview has expired: preview again";
    case "preview_already_actioned": return "This preview was already sent or cancelled";
    case "preview_state_not_valid": return "The preview is not valid for submit";
    case "regime_pre_event": return "The regime is pre-event: entries are paused";
    case "signal_flipped": return "The signal has flipped since the preview";
    case "signal_no_longer_actionable": return "The signal is no longer actionable";
    default: return code.replace(/_/g, " ");
  }
}

/** Every reason of a refusal, from the body of an API error. */
export function refusalReasons(e: unknown): string[] {
  if (!(e instanceof ApiError)) return [e instanceof Error ? e.message : String(e)];
  const detail = (e.body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return [detail];
  const d = dict(detail);
  if (Object.keys(d).length === 0) return [`request failed (${e.status})`];
  if (d.error === "pretrade_gate_failed" && Array.isArray(d.failures)) {
    return (d.failures as unknown[]).map((c) => explainGateFailure(String(c), d.details));
  }
  if (d.error === "revalidation_failed" && typeof d.reason === "string") {
    return [explainGateFailure(d.reason, d.details)];
  }
  if (typeof d.message === "string") return [d.message];
  const parts: string[] = [];
  if (typeof d.error === "string") parts.push(d.error.replace(/_/g, " "));
  if (typeof d.reason === "string") parts.push(d.reason.replace(/_/g, " "));
  if (typeof d.exception === "string") parts.push(d.exception);
  else if (typeof d.body === "string" && d.body) parts.push(d.body);
  if (typeof d.trace_id === "string") parts.push(`trace ${d.trace_id}`);
  return parts.length > 0 ? [parts.join(": ")] : [`request failed (${e.status})`];
}

/** One line for places that hold a single string. */
export const refusalText = (e: unknown): string => refusalReasons(e).join(" · ");

/** A row of the gate's verdict carried by a preview (`risk_budget.rows`). */
export interface BudgetRow {
  key: string;
  label: string;
  unit: string;
  cap: number | null;
  before: number | null;
  trade: number | null;
  after: number | null;
  used: number | null;
  breached: boolean;
  warning?: boolean; // inside the limit, above its warning share
}
export interface RiskBudget {
  rows: BudgetRow[];
  failures: string[];
  details?: Dict;
  warnings?: string[];
  max_qty?: number | null; // largest size of this trade inside every limit
  qty?: number | null;
}

export const fmtBudget = (row: BudgetRow, v: number | null): string => {
  if (v == null) return "—";
  if (row.key === "notional") return "€" + (v / 1e6).toFixed(2) + "M";
  return usd(v);
};
