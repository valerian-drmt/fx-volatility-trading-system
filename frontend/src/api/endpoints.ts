// Typed helpers built on top of the generated OpenAPI schema. Any drift between
// FastAPI Pydantic models and `schema.d.ts` is caught at `tsc --noEmit` time
// (or earlier by `npm run gen:api:check` in CI).
import type { paths } from "./schema";
import { apiGet, apiPost, apiPut } from "./client";
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
export type GreeksResponse = Post<"/api/v1/greeks", 200>;
export type IvRequest = PostBody<"/api/v1/iv">;
export type IvResponse = Post<"/api/v1/iv", 200>;

export const postPrice = (body: PriceRequest) =>
  apiPost<PriceResponse>("/api/v1/price", body);
export const postGreeks = (body: PriceRequest) =>
  apiPost<GreeksResponse>("/api/v1/greeks", body);
export const postIv = (body: IvRequest) => apiPost<IvResponse>("/api/v1/iv", body);

// ── Vol ───────────────────────────────────────────────────────────────────
export type VolSurface = Get<"/api/v1/vol/surface", 200>;
export type TermStructure = Get<"/api/v1/vol/term-structure", 200>;
export type Smile = Get<"/api/v1/vol/smile/{tenor}", 200>;

export const fetchVolSurface = (symbol = "EURUSD") =>
  apiGet<VolSurface>("/api/v1/vol/surface", { query: { symbol } });
export const fetchTermStructure = (symbol = "EURUSD") =>
  apiGet<TermStructure>("/api/v1/vol/term-structure", { query: { symbol } });
export const fetchSmile = (tenor: string, symbol = "EURUSD") =>
  apiGet<Smile>(`/api/v1/vol/smile/${encodeURIComponent(tenor)}`, { query: { symbol } });

// ── Portfolio ─────────────────────────────────────────────────────────────
export type Positions = Get<"/api/v1/positions", 200>;
export type Position = Get<"/api/v1/positions/{position_id}", 200>;
export type Risk = Get<"/api/v1/risk", 200>;
export type PnlCurve = Get<"/api/v1/pnl-curve", 200>;

export const fetchPositions = (params?: { status?: string; limit?: number }) =>
  apiGet<Positions>("/api/v1/positions", params ? { query: params } : {});
export const fetchPosition = (id: number) =>
  apiGet<Position>(`/api/v1/positions/${id}`);
export const fetchRisk = () => apiGet<Risk>("/api/v1/risk");
export const fetchPnlCurve = () => apiGet<PnlCurve>("/api/v1/pnl-curve");

// ── Analytics ─────────────────────────────────────────────────────────────
// Per-tenor pricing signals (CHEAP/FAIR/EXPENSIVE) and the /signals endpoint
// were retired in R9 alongside the Vol Scanner panel.
export type VolHistory = Get<"/api/v1/vol-history", 200>;
export type SystemStats = Get<"/api/v1/system-stats", 200>;

export const fetchVolHistory = (symbol = "EURUSD", limit = 50) =>
  apiGet<VolHistory>("/api/v1/vol-history", { query: { symbol, limit } });

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

export const fetchSystemStats = () => apiGet<SystemStats>("/api/v1/system-stats");

// ── R11 voldesk wiring (read) ───────────────────────────────────────────────
// Typed against schema.d.ts. Endpoints returning a bare dict server-side surface
// here as opaque values; the per-domain adapter (voldesk/data/live/*) maps them
// to the DATA/DATA2 shapes the views consume.

// Regime
export type RegimeState = Get<"/api/v1/regime/state", 200>;
export const fetchRegimeState = (symbol = "EURUSD") =>
  apiGet<RegimeState>("/api/v1/regime/state", { query: { symbol } });
export const fetchRegimeEvents = (n = 10) =>
  apiGet<unknown>("/api/v1/regime/events", { query: { n } });

// PCA signals
export type PcaState = Get<"/api/v1/signals/pca/state", 200>;
export type PcaModel = Get<"/api/v1/signals/pca/model", 200>;
export const fetchPcaState = (symbol = "EURUSD") =>
  apiGet<PcaState>("/api/v1/signals/pca/state", { query: { symbol } });
export const fetchPcaModel = () => apiGet<PcaModel>("/api/v1/signals/pca/model");
export type PcaHistory = Get<"/api/v1/signals/pca/history", 200>;
export const fetchPcaHistory = (pcId: number, n = 120, symbol = "EURUSD") =>
  apiGet<PcaHistory>("/api/v1/signals/pca/history", { query: { symbol, pc_id: pcId, n } });

// Positions (Step 5)
export const fetchOpenPositions = () => apiGet<unknown>("/api/v1/positions/open");
export const fetchActivePositions = () => apiGet<unknown>("/api/v1/positions/active");
export const fetchPositionsAggregate = () =>
  apiGet<unknown>("/api/v1/positions/aggregate");

// Portfolio panels (A–J + scenarios)
export const fetchPortfolioHeader = () => apiGet<unknown>("/api/v1/portfolio/header");
export const fetchPortfolioAccount = () => apiGet<unknown>("/api/v1/portfolio/account");
export const fetchPortfolioCash = () => apiGet<unknown>("/api/v1/portfolio/cash");
export const fetchPortfolioDailyPnl = (days = 90) =>
  apiGet<unknown>("/api/v1/portfolio/daily-pnl", { query: { days } });
export const fetchPortfolioStats = () => apiGet<unknown>("/api/v1/portfolio/stats");
export const fetchPortfolioVar = () => apiGet<unknown>("/api/v1/portfolio/var");
export const fetchGreekLimits = () => apiGet<unknown>("/api/v1/portfolio/greek-limits");
export const fetchRiskPerTenor = () => apiGet<unknown>("/api/v1/portfolio/risk-per-tenor");
export const fetchEquityCurve = (window = "30d") =>
  apiGet<unknown>("/api/v1/portfolio/equity-curve", { query: { window } });
export const fetchAggregateGreeks = () =>
  apiGet<unknown>("/api/v1/portfolio/aggregate-greeks");
export const fetchVegaPerTenor = () =>
  apiGet<unknown>("/api/v1/portfolio/vega-per-tenor");
export const fetchStressGrid = (axis = "spot-vol", output = "pnl") =>
  apiGet<unknown>("/api/v1/portfolio/stress-grid", { query: { axis, output } });
export const fetchGreeksLadder = (axis = "spot") =>
  apiGet<unknown>("/api/v1/portfolio/greeks-ladder", { query: { axis } });
export const fetchPnlAttribution = (lookbackHours = 24) =>
  apiGet<unknown>("/api/v1/portfolio/pnl-attribution", {
    query: { lookback_hours: lookbackHours },
  });
export const fetchPinRisk = () => apiGet<unknown>("/api/v1/portfolio/pin-risk");
export const fetchVegaPca = () => apiGet<unknown>("/api/v1/portfolio/vega-pca");
export const fetchMarginalVar = () => apiGet<unknown>("/api/v1/portfolio/marginal-var");
export const fetchVarFactors = () => apiGet<unknown>("/api/v1/portfolio/var-factors");
export const fetchScenarios = () => apiGet<unknown>("/api/v1/portfolio/scenarios");
export const fetchHedgeSummary = () =>
  apiGet<unknown>("/api/v1/portfolio/hedge-summary");

// Trade (read)
// Working orders = live IB openTrades, proxied via the execution-engine.
export const fetchOrders = () => apiGet<unknown>("/api/v1/orders");
export const fetchTradeStructures = () => apiGet<unknown>("/api/v1/trade/structures");
export const fetchTradeLimits = () => apiGet<unknown>("/api/v1/trade/limits");
export const fetchTradeBook = (symbol = "EURUSD") =>
  apiGet<unknown>("/api/v1/trade/book", { query: { symbol } });

// Admin config (read; write lands in Phase 2 behind auth)
export type ConfigResponse = Get<"/api/v1/admin/config", 200>;
export const fetchConfig = () => apiGet<ConfigResponse>("/api/v1/admin/config");
export const fetchConfigSchema = () => apiGet<unknown>("/api/v1/admin/config/schema");
export const fetchConfigHistory = (limit = 50) =>
  apiGet<unknown>("/api/v1/admin/config/history", { query: { limit } });
// Settings write (Phase 2 / 2w) — gated by auth in prod (require_write), free locally.
export const revertConfig = (version: number, comment?: string) =>
  apiPost<unknown>(`/api/v1/admin/config/revert/${version}`, { user: "trader", comment });
export const putConfig = (patch: Record<string, unknown>, comment?: string) =>
  apiPut<unknown>("/api/v1/admin/config", { patch, user: "trader", comment });
export const fetchDomainSettings = (domain: string) =>
  apiGet<unknown>(`/api/v1/admin/settings/${domain}`);
export const putDomainSettings = (domain: string, updates: Record<string, number>) =>
  apiPut<unknown>(`/api/v1/admin/settings/${domain}`, { updates, user: "trader" });

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
  pricing?: {
    premium_paid_usd?: number;
    max_loss_usd?: number;
    max_loss_at_expiry_only?: boolean;
  };
  greeks_net?: Record<string, number | Record<string, number>>;
}

export const createTradePreview = (legs: PreviewLeg[], qty: number, symbol = "EURUSD") =>
  apiPost<TradePreview>("/api/v1/trade/preview", { legs, qty }, { query: { symbol } });

/** Submit a previewed structure. execution_mode "live" = IB paper account. */
export const submitTrade = (previewId: string, executionMode: "live" | "mock" = "live") =>
  apiPost<Record<string, unknown>>("/api/v1/trade/submit", {
    preview_id: previewId,
    execution_mode: executionMode,
  });

export const cancelTradePreview = (previewId: string) =>
  apiPost<unknown>(`/api/v1/trade/preview/${encodeURIComponent(previewId)}/cancel`, {});

// Persisted submitted structures (DB: trade_structure + booked_position). Read-only,
// so the Orders blotter survives refresh and reflects the real DB state.
export interface SubmittedTrade {
  id: number;
  created_at: string;
  structure_type: string | null;
  product_label: string | null;
  contract: string | null; // IB localSymbol(s) of the legs ("EUUV6 C1130", or "… +N")
  reference_tenor: string | null;
  base_qty: number | null;
  state: string | null;
  execution_mode: string | null;
  position_state: string | null;
  order_role: string | null; // entry / closing / unwind / hedge
}
export const fetchSubmitted = (limit = 50) =>
  apiGet<SubmittedTrade[]>("/api/v1/trade/submitted", { query: { limit } });

// Booking-driven positions: structures grouped by trade_structure (real product +
// tenor + legs), live marks attached, plus IB positions not tied to any booking.
export interface StructuredLeg {
  leg_idx: number; contract_type: string; side: string; qty: number;
  strike: number | null; expiry: string | null; state: string; qty_filled: number;
  ib_local_symbol: string | null; linked: boolean; mark: number | null; pnl_usd: number | null;
  delta_usd: number | null; gamma_usd: number | null; vega_usd: number | null;
  theta_usd: number | null; vanna_usd: number | null; volga_usd: number | null; iv: number | null;
  // Live IB-mirror identity (present only when `linked`). position_id is the
  // open_position row the UI closes by.
  position_id: number | null; con_id: number | null; held_qty: number | null; held_side: string | null;
  entry: number | null; nominal_eur: number | null; tenor: string | null;
  opened: string | null; updated: string | null;
}
export interface StructuredStructure {
  structure_id: number; structure_type: string; product_label: string | null;
  tenor: string; state: string; base_qty: number; created_at: string | null;
  legs: StructuredLeg[];
  net: { delta_usd: number; gamma_usd: number; vega_usd: number; theta_usd: number; pnl_usd: number; n_linked: number };
}
export interface UnlinkedPosition {
  id: number; symbol: string; product_label: string | null; side: string;
  qty: number | null; tenor: string | null; expiry: string | null;
  mark: number | null; pnl_usd: number | null; delta_usd: number | null;
  gamma_usd: number | null; vega_usd: number | null; theta_usd: number | null;
  vanna_usd: number | null; volga_usd: number | null; iv: number | null;
}
export interface StructuredPositions { structures: StructuredStructure[]; unlinked: UnlinkedPosition[] }
export const fetchStructuredPositions = () =>
  apiGet<StructuredPositions>("/api/v1/positions/structured");

/** Close a single leg (OpenPosition.id). Partial close via `qty`. */
export const closeContract = (positionId: number, qty: number) =>
  apiPost<Record<string, unknown>>(`/api/v1/positions/${positionId}/close`, { qty });

/** Close every open leg of a trade (OpenPosition.trade_id). */
export const closeTrade = (tradeId: number) =>
  apiPost<Record<string, unknown>>(`/api/v1/trades/${tradeId}/close`, {});

// Dev / system
export const fetchDevEngines = () => apiGet<unknown>("/api/v1/dev/engines");
export const fetchCycleProgress = () => apiGet<unknown>("/api/v1/dev/cycle-progress");
