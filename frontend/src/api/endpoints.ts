// Typed helpers built on top of the generated OpenAPI schema. Any drift between
// FastAPI Pydantic models and `schema.d.ts` is caught at `tsc --noEmit` time
// (or earlier by `npm run gen:api:check` in CI).
import type { RiskBudget } from "../voldesk/components/refusal";
import type { paths } from "./schema";
import { apiDelete, apiGet, apiPost, apiPut } from "./client";
export { ApiError } from "./client";

type Get<P extends keyof paths, S extends 200> = paths[P] extends {
  get: { responses: { [K in S]: { content: { "application/json": infer R } } } };
}
  ? R
  : never;

type Post<P extends keyof paths, S extends 200> = paths[P] extends {
  post: { responses: { [K in S]: { content: { "application/json": infer R } } } };
}
  ? R
  : never;

type Put<P extends keyof paths, S extends 200> = paths[P] extends {
  put: { responses: { [K in S]: { content: { "application/json": infer R } } } };
}
  ? R
  : never;

type PostBody<P extends keyof paths> = paths[P] extends {
  post: { requestBody: { content: { "application/json": infer B } } };
}
  ? B
  : never;

// ── Auth (single-trader) ─────────────────────────────────────────────────────
// Reads are public; logging in sets the httpOnly fxvol_auth cookie that unlocks
// the write endpoints (require_write). `credentials:"include"` (client.ts) sends it.
export type AuthStatus = Get<"/api/v1/auth/me", 200>;
export type LoginBody = PostBody<"/api/v1/auth/login">;
export const fetchAuthMe = () => apiGet<AuthStatus>("/api/v1/auth/me");
export const postLogin = (body: LoginBody) =>
  apiPost<AuthStatus>("/api/v1/auth/login", body);
export const postLogout = () => apiPost<AuthStatus>("/api/v1/auth/logout", {});

// ── Health ────────────────────────────────────────────────────────────────
export type Health = Get<"/api/v1/health", 200>;
export type HealthExtended = Get<"/api/v1/health/extended", 200>;
export const fetchHealth = () => apiGet<Health>("/api/v1/health");
export const fetchHealthExtended = () =>
  apiGet<HealthExtended>("/api/v1/health/extended");

// ── Pricing ───────────────────────────────────────────────────────────────
export type PriceRequest = PostBody<"/api/v1/price">;
export type PriceResponse = Post<"/api/v1/price", 200>;

export const postPrice = (body: PriceRequest) =>
  apiPost<PriceResponse>("/api/v1/price", body);

// ── Vol ───────────────────────────────────────────────────────────────────
export type VolSurface = Get<"/api/v1/vol/surface", 200>;
export type TermStructure = Get<"/api/v1/vol/term-structure", 200>;
export type Smile = Get<"/api/v1/vol/smile/{tenor}", 200>;

export const fetchVolSurface = (symbol = "EURUSD") =>
  apiGet<VolSurface>("/api/v1/vol/surface", { query: { symbol } });
export const fetchTermStructure = (symbol = "EURUSD") =>
  apiGet<TermStructure>("/api/v1/vol/term-structure", { query: { symbol } });

// ── Analytics ─────────────────────────────────────────────────────────────
// Per-tenor pricing signals (CHEAP/FAIR/EXPENSIVE) and the /signals endpoint
// were retired in R9 alongside the Vol Scanner panel.
// Real OHLC candles from the market-data engine's Redis cache (IB reqHistoricalData).
// Typed by hand (not via schema.d.ts) until `npm run gen:api` is regenerated
// against the running backend; `t` = bar-open epoch ms (UTC).
export interface Bar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
}
export const fetchBars = (symbol = "EURUSD", tf = "1h", limit = 48) =>
  apiGet<Bar[]>("/api/v1/bars", { query: { symbol, tf, limit } });

// Trade open/close events for the Performance ticker overlay (booked positions).
export interface TradeMarkerRaw {
  id: number;
  type: string;
  opened_at: string | null;
  entry_spot: number | null;
  closed_at: string | null;
  net_pnl_usd: number | null;
  state: string;
}
export const fetchTradeMarkers = (days = 30) =>
  apiGet<TradeMarkerRaw[]>("/api/v1/portfolio/trade-markers", { query: { days } });

// Cumulative greek-P&L (Taylor terms) time series for the Performance 2×2 grid.
export interface GreekPnlHistoryRaw {
  timestamp: string;
  delta_pnl_usd: number;
  gamma_pnl_usd: number;
  vega_pnl_usd: number;
  theta_pnl_usd: number;
}
export const fetchGreekPnlHistory = (window = "30d") =>
  apiGet<GreekPnlHistoryRaw[]>("/api/v1/portfolio/greek-pnl-history", { query: { window } });

// Net-liq valuation decomposition (USD cash / EUR cash $ / contracts) time series.
export interface ValuationHistoryRaw {
  timestamp: string;
  net_liq_usd: number | null;
  usd_cash_usd: number | null;
  eur_cash_usd: number | null;
  contracts_usd: number | null;
}
export const fetchValuationHistory = (window = "30d") =>
  apiGet<ValuationHistoryRaw[]>("/api/v1/portfolio/valuation-history", { query: { window } });

// ── R11 voldesk wiring (read) ───────────────────────────────────────────────
// Every helper is typed from schema.d.ts (#348): a renamed or removed path, or
// an object that became a list, fails `tsc`. Endpoints whose router returns a
// bare dict surface as Record<string, unknown> (or a list of them); the
// per-domain adapter (voldesk/data/live/*) narrows them to the view shapes.

// Regime
export const fetchRegimeEvents = (n = 10, pastDays = 0) =>
  apiGet<Get<"/api/v1/regime/events", 200>>("/api/v1/regime/events", { query: { n, past_days: pastDays } });

// PCA signals
export type PcaState = Get<"/api/v1/signals/pca/state", 200>;
export const fetchPcaState = (symbol = "EURUSD") =>
  apiGet<PcaState>("/api/v1/signals/pca/state", { query: { symbol } });
export type PcaHistory = Get<"/api/v1/signals/pca/history", 200>;
export const fetchPcaHistory = (pcId: number, n = 120, symbol = "EURUSD") =>
  apiGet<PcaHistory>("/api/v1/signals/pca/history", { query: { symbol, pc_id: pcId, n } });

// Positions (Step 5)
export const fetchOpenPositions = () => apiGet<Get<"/api/v1/positions/open", 200>>("/api/v1/positions/open");

// Portfolio panels (A–J + scenarios)
export const fetchPortfolioAccount = () => apiGet<Get<"/api/v1/portfolio/account", 200>>("/api/v1/portfolio/account");
export const fetchPortfolioCash = () => apiGet<Get<"/api/v1/portfolio/cash", 200>>("/api/v1/portfolio/cash");
export const fetchPortfolioDailyPnl = (days = 90) =>
  apiGet<Get<"/api/v1/portfolio/daily-pnl", 200>>("/api/v1/portfolio/daily-pnl", { query: { days } });
export const fetchPortfolioStats = () => apiGet<Get<"/api/v1/portfolio/stats", 200>>("/api/v1/portfolio/stats");
export const fetchPortfolioVar = () => apiGet<Get<"/api/v1/portfolio/var", 200>>("/api/v1/portfolio/var");
export const fetchRiskPerTenor = () => apiGet<Get<"/api/v1/portfolio/risk-per-tenor", 200>>("/api/v1/portfolio/risk-per-tenor");
export const fetchEquityCurve = (window = "30d") =>
  apiGet<Get<"/api/v1/portfolio/equity-curve", 200>>("/api/v1/portfolio/equity-curve", { query: { window } });
export const fetchVegaPerTenor = () =>
  apiGet<Get<"/api/v1/portfolio/vega-per-tenor", 200>>("/api/v1/portfolio/vega-per-tenor");
export const fetchStressGrid = (axis = "spot-vol", output = "pnl") =>
  apiGet<Get<"/api/v1/portfolio/stress-grid", 200>>("/api/v1/portfolio/stress-grid", { query: { axis, output } });
export const fetchGreeksLadder = (axis = "spot") =>
  apiGet<Get<"/api/v1/portfolio/greeks-ladder", 200>>("/api/v1/portfolio/greeks-ladder", { query: { axis } });
export const fetchPnlAttribution = (lookbackHours = 24) =>
  apiGet<Get<"/api/v1/portfolio/pnl-attribution", 200>>("/api/v1/portfolio/pnl-attribution", {
    query: { lookback_hours: lookbackHours },
  });

// Same decomposition, bucketed by an axis (tenor / structure) → P&L-attribution matrix.
export const fetchPnlAttributionMatrix = (groupBy = "tenor", lookbackHours = 24) =>
  apiGet<Get<"/api/v1/portfolio/pnl-attribution", 200>>("/api/v1/portfolio/pnl-attribution", {
    query: { group_by: groupBy, lookback_hours: lookbackHours },
  });
export const fetchPinRisk = () => apiGet<Get<"/api/v1/portfolio/pin-risk", 200>>("/api/v1/portfolio/pin-risk");
export const fetchMarginalVar = () => apiGet<Get<"/api/v1/portfolio/marginal-var", 200>>("/api/v1/portfolio/marginal-var");

// Trade (read)
// Spot FX (EUR.USD cash) market order — write-gated proxy to the
// execution-engine (/internal/orders). qty = base-currency notional.
export interface SpotOrderBody {
  symbol: string;
  sec_type: "CASH";
  side: "BUY" | "SELL";
  qty: number;
  exchange: string;
  currency: string;
  // Marketable limit (SELL bid-2p / BUY ask+2p). Omitted = IB market order,
  // which IDEALPRO simulates and may park in PreSubmitted (#290) — the ticket
  // always sends one.
  limit_price?: number;
}
export const postSpotOrder = (body: SpotOrderBody) =>
  apiPost<{ spot_order_id?: number; order_id?: number; status?: string }>("/api/v1/orders", body);
/** Cancel an IB order by its IB orderId (spot rows carry it as ib_order_id). */
export const cancelIbOrder = (ibOrderId: number) =>
  apiDelete<Record<string, unknown>>(`/api/v1/orders/${ibOrderId}`);

// Persisted spot FX (CASH) orders from the cash ticket (DB: spot_order, #291).
export interface SpotOrderRow {
  id: number;
  created_at: string;
  symbol: string;
  currency: string;
  local_symbol: string; // "EUR.USD"
  side: "BUY" | "SELL" | string; // base-ccy side: BUY = buy EUR / sell USD
  qty: number; // base-ccy notional (EUR)
  qty_filled: number;
  order_type: string; // "LMT" / "MKT"
  limit_price: number | null;
  avg_fill_price: number | null;
  state: string; // pending / submitted / partially_filled / filled / cancelled / rejected
  state_updated_at?: string | null; // ordering key for the push patch (#367)
  rejection_text: string | null;
  ib_order_id: number | null;
  ib_status?: string | null; // IB's raw status (PreSubmitted / Submitted / ...) (#376)
  why_held?: string | null; // IB's whyHeld hint for a held order
}
export const fetchSpotOrders = (limit = 30) =>
  apiGet<SpotOrderRow[]>("/api/v1/trade/spot-orders", { query: { limit } });
export const fetchTradeLimits = () => apiGet<Get<"/api/v1/trade/limits", 200>>("/api/v1/trade/limits");
export const fetchTradeBook = (symbol = "EURUSD") =>
  apiGet<Get<"/api/v1/trade/book", 200>>("/api/v1/trade/book", { query: { symbol } });

// Admin config (read; write lands in Phase 2 behind auth)
export type ConfigResponse = Get<"/api/v1/admin/config", 200>;
export const fetchConfig = () => apiGet<ConfigResponse>("/api/v1/admin/config");
export const fetchConfigHistory = (limit = 50) =>
  apiGet<Get<"/api/v1/admin/config/history", 200>>("/api/v1/admin/config/history", { query: { limit } });
// Settings write (Phase 2 / 2w) — gated by auth in prod (require_write), free locally.
export const revertConfig = (version: number, comment?: string) =>
  apiPost<Post<"/api/v1/admin/config/revert/{version}", 200>>(`/api/v1/admin/config/revert/${version}`, { user: "trader", comment });
export const putConfig = (patch: Record<string, unknown>, comment?: string) =>
  apiPut<Put<"/api/v1/admin/config", 200>>("/api/v1/admin/config", { patch, user: "trader", comment });
export const fetchDomainSettings = (domain: string) =>
  apiGet<Get<"/api/v1/admin/settings/{domain}", 200>>(`/api/v1/admin/settings/${domain}`);
export const putDomainSettings = (domain: string, updates: Record<string, number>) =>
  apiPut<Put<"/api/v1/admin/settings/{domain}", 200>>(`/api/v1/admin/settings/${domain}`, { updates, user: "trader" });

// ── Trade write (Phase 2 / 6w) — submit + close ──────────────────────────────
// Paper-first: `execution_mode:"live"` routes to the IB *paper* account (the
// system stays READ_ONLY_API until an explicit go-live, see docs/strategy.md §5).
// Gated by auth in prod (require_write); free locally behind VITE_WRITE_ENABLED.

/** One free-composed leg sent to POST /trade/preview (mirrors backend LegSpec). */
export interface PreviewLeg {
  contract_type: "call" | "put" | "future";
  side: "BUY" | "SELL";
  tenor: string;
  delta_pillar?: string;                 // 10dp/25dp/atm/25dc/10dc (options)
  strike?: number;                       // explicit strike override (options)
  qty_factor?: number;                   // relative weight; ×base_qty at sizing
  future_contract_size?: "full" | "micro";
}

/** Subset of the /trade/preview payload the desk reads (server returns a dict). */
export interface TradePreview {
  preview_id: string;
  state: string;                         // "valid_for_submit" | "blocked" | …
  blocking_reasons?: string[];
  structure?: { type?: string };
  costs?: { commission_usd?: number; total_trade_cost_usd?: number };
  pricing?: {
    premium_paid_usd?: number;
    max_loss_usd?: number;
    max_loss_at_expiry_only?: boolean;
  };
  greeks_net?: Record<string, number | Record<string, number>>;
  // What the submit would refuse right now, and the gate's verdict on this
  // trade: per limit, cap / book before / trade / book after (#478, #479).
  submit_blockers?: string[];
  surface_age_seconds?: number | null;
  max_surface_age_seconds?: number | null;
  risk_budget?: RiskBudget | null;
  // The package's hedge, sized by the server (#482): the futures the engine
  // sends as the options fill. null when the trade is not hedged.
  hedge?: { side: string; qty: number; residual_delta_usd: number; structure_delta_usd: number } | null;
  // The trade is inside the limits but above their warning share (#481).
  needs_confirmation?: boolean;
}

export const createTradePreview = (legs: PreviewLeg[], qty: number, symbol = "EURUSD", hedge = false) =>
  apiPost<TradePreview>("/api/v1/trade/preview", { legs, qty, hedge }, { query: { symbol } });

/** Submit a previewed structure. execution_mode "live" = IB paper account. */
export const submitTrade = (previewId: string, executionMode: "live" | "mock" = "live", confirmLimits = false) =>
  apiPost<Record<string, unknown>>("/api/v1/trade/submit", {
    preview_id: previewId,
    execution_mode: executionMode,
    confirm_limits: confirmLimits,
  });

// Persisted submitted structures (DB: trade_structure + booked_position). Read-only,
// so the Orders blotter survives refresh and reflects the real DB state.
export interface SubmittedLeg {
  leg_idx: number;
  contract: string | null; // IB localSymbol of the leg ("EUUV6 C1130")
  contract_type: string | null; // "call" / "put" / "future"
  strike: number | null;
  side: string | null; // "BUY" / "SELL"
  qty: number; // ordered contracts for this leg
  qty_filled: number; // filled so far
  state: string; // per-leg order state
  rejection_text?: string | null; // IB's reason when it refused or cancelled the order
  order_id?: number; // trade_order id, to cancel this leg only
}
export interface SubmittedTrade {
  id: number;
  created_at: string;
  structure_type: string | null;
  product_label: string | null;
  contract: string | null; // IB localSymbol(s) of the legs ("EUUV6 C1130", or "… +N")
  reference_tenor: string | null;
  base_qty: number | null;
  qty_total?: number | null; // total contracts across the structure's legs
  qty_filled?: number | null; // contracts filled so far (for in-flight progress)
  legs?: SubmittedLeg[]; // per-leg detail — the blotter renders one row per leg
  state: string | null;
  execution_mode: string | null;
  position_state: string | null;
  order_role: string | null; // entry / closing / unwind / hedge
  closes_trade_id?: number | null; // for a close: the original trade it closes (#30, not this #31)
}
export const fetchSubmitted = (limit = 50) =>
  apiGet<SubmittedTrade[]>("/api/v1/trade/submitted", { query: { limit } });

// Booking-driven positions: structures grouped by trade_structure (real product +
// tenor + legs), live marks attached, plus IB positions not tied to any booking.
export interface StructuredLeg {
  leg_idx: number; contract_type: string; side: string; qty: number;
  strike: number | null; expiry: string | null; state: string; qty_filled: number;
  ib_local_symbol: string | null; linked: boolean;
  // open = really held per the BOOK (leg_position), even when the netted IB mirror
  // can't show it (two trades on opposite sides of one contract net to 0 at IB).
  // open_qty is the signed book holding. Render on `open`, not `linked`.
  open?: boolean; open_qty?: number | null;
  mark: number | null; pnl_usd: number | null;
  delta_usd: number | null; gamma_usd: number | null; vega_usd: number | null;
  theta_usd: number | null; vanna_usd: number | null; volga_usd: number | null; iv: number | null;
  // Live IB-mirror identity (present only when `linked`). position_id is the
  // open_position row the UI closes by.
  position_id: number | null; con_id: number | null; held_qty: number | null; held_side: string | null;
  entry: number | null; nominal_eur: number | null; tenor: string | null;
  opened: string | null; updated: string | null;
  // The position line this leg carries (#461), null when it carries none.
  line_id?: string | null; line_status?: string | null;
  book_qty?: number | null; broker_qty?: number | null;
  order_role?: string; booked_expiry?: string | null;
  identity?: unknown;
}
export interface StructuredStructure {
  structure_id: number; structure_type: string; product_label: string | null;
  tenor: string; state: string; base_qty: number; created_at: string | null;
  legs: StructuredLeg[];
  net: {
    delta_usd: number; gamma_usd: number; vega_usd: number; theta_usd: number; pnl_usd: number;
    n_linked: number; n_lines?: number;
  };
}
export interface UnlinkedPosition {
  id: number; symbol: string; product_label: string | null; side: string;
  qty: number | null; tenor: string | null; expiry: string | null;
  mark: number | null; pnl_usd: number | null; delta_usd: number | null;
  gamma_usd: number | null; vega_usd: number | null; theta_usd: number | null;
  vanna_usd: number | null; volga_usd: number | null; iv: number | null;
  entry?: number | null;
  line_id?: string | null; line_status?: string | null;
  book_qty?: number | null; broker_qty?: number | null;
  identity?: unknown;
}
export interface StructuredPositions { structures: StructuredStructure[]; unlinked: UnlinkedPosition[] }
export const fetchStructuredPositions = () =>
  apiGet<StructuredPositions>("/api/v1/positions/structured");

/** Close a single leg (OpenPosition.id). Partial close via `qty`. */
export const closeContract = (positionId: number, qty: number) =>
  apiPost<Record<string, unknown>>(`/api/v1/positions/${positionId}/close`, { qty });

/** Close every open leg of a trade (OpenPosition.trade_id). */
export interface TradeCloseResult {
  closed_legs?: number;
  failed_legs?: number;
  legs?: { local_symbol?: string; ok?: boolean; error?: string }[];
}
export const closeTrade = (tradeId: number) =>
  apiPost<TradeCloseResult>(`/api/v1/trades/${tradeId}/close`, {});

/** Cancel every non-terminal order of a trade at IB + terminalise the structure. */
/** Cancel a trade's working orders, or one leg of it (`orderId`). IB's
 * confirmation ends the order; `cancel_requested` counts the ones still
 * waiting for it. */
export const cancelTrade = (structureId: number, orderId?: number) =>
  apiPost<{ cancelled?: number; cancel_requested?: number }>(
    `/api/v1/trades/${structureId}/cancel`, {}, orderId != null ? { query: { order_id: orderId } } : undefined,
  );

/** Orders our own checks refused before the broker (gate, revalidation). */
export interface Refusal {
  id: number;
  ts: string;
  kind: string;
  failures: string[];
  details: Record<string, unknown> | null;
  preview_id?: string | null;
}
export const fetchRefusals = (limit = 20) =>
  apiGet<Refusal[]>("/api/v1/trade/refusals", { query: { limit } });

// Dev / system
export const fetchDevEngines = () => apiGet<Get<"/api/v1/dev/engines", 200>>("/api/v1/dev/engines");
export const fetchCycleProgress = () => apiGet<Get<"/api/v1/dev/cycle-progress", 200>>("/api/v1/dev/cycle-progress");
