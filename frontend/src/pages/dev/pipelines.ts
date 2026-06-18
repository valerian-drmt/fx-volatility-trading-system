/**
 * Declarative end-to-end pipeline per PROD panel (dev "Pipeline" tab).
 *
 * The prod front (voldesk) has 5 tabs; this lists EVERY panel across them. Each
 * panel → one left→right plumbing diagram: source → … → api → frontend → the
 * panel (terminal block). The "current" flows when the panel's data is live; a
 * down block goes red. `edges.length === nodes.length - 1`.
 *
 * The Spot ticker is fully wired to real per-block health (see `health` keys).
 * Other panels resolve from their `domain` freshness (uniform) until wired the
 * same way. `health` per node: an engine label / stack item name, or "__self"
 * (always up) / "__ws" (panel WS feed) / "__api" (api responds); omitted → domain.
 */
export type ViewId = "dashboard" | "trade" | "signals" | "risk" | "portfolio";

export type DomainId =
  | "surface" | "pca" | "trade" | "portfolio" | "risk" | "termStructure" | "ticks" | "system";

export type NodeKind = "external" | "container" | "store" | "api" | "frontend" | "panel";

export interface PipeNode {
  kind: NodeKind;
  label: string;
  sub?: string;
  health?: string;
}

export interface PanelPipe {
  id: string;
  panel: string;
  view: ViewId;
  domain: DomainId;
  nodes: PipeNode[];
  edges: string[];
}

const IB: PipeNode = { kind: "external", label: "IB", sub: "Interactive Brokers" };
const IBG: PipeNode = { kind: "container", label: "ib-gateway", sub: "broker session" };
const API: PipeNode = { kind: "api", label: "api", sub: "FastAPI" };
const FE: PipeNode = { kind: "frontend", label: "frontend", sub: "React · fetch/WS" };
const pg = (sub: string): PipeNode => ({ kind: "store", label: "Postgres", sub });
const redis = (sub: string): PipeNode => ({ kind: "store", label: "Redis", sub });
const eng = (label: string, sub: string): PipeNode => ({ kind: "container", label, sub });
const panel = (name: string): PipeNode => ({ kind: "panel", label: name, sub: "displayed panel" });
const DBW = eng("db-writer", "db_events → batch INSERT");

export const PIPELINES: PanelPipe[] = [
  // ───────────────────────── Dashboard ─────────────────────────
  {
    id: "ticker", panel: "Spot ticker (bid/ask)", view: "dashboard", domain: "ticks",
    nodes: [
      { kind: "external", label: "IB", sub: "Interactive Brokers", health: "IB Gateway" },
      { kind: "container", label: "ib-gateway", sub: "broker session", health: "IB Gateway" },
      { kind: "container", label: "market-data", sub: "clientId 1 · tick stream", health: "market-data" },
      { kind: "store", label: "Redis", sub: "latest_spot:EUR · ticks ch.", health: "redis" },
      { kind: "api", label: "api", sub: "FastAPI · WS bridge", health: "__api" },
      { kind: "frontend", label: "frontend", sub: "React · WS client", health: "__self" },
      { kind: "panel", label: "Ticker bid/ask", sub: "displayed panel", health: "__ws" },
    ],
    edges: ["get tick", "reqMktData", "publish ticks", "WS bridge", "WS /ws/ticks", "render"],
  },
  {
    id: "dash-market", panel: "Market snapshot", view: "dashboard", domain: "ticks",
    nodes: [IB, IBG, eng("market-data", "clientId 1"), redis("latest_spot:EUR"), API, FE, panel("Market snapshot")],
    edges: ["get tick", "reqMktData", "publish", "WS bridge", "WS /ws/ticks", "render"],
  },
  {
    id: "dash-signal", panel: "Active signal", view: "dashboard", domain: "pca",
    nodes: [eng("vol-engine", "PCA projection"), pg("pca_signal_history"), API, FE, panel("Active signal")],
    edges: ["persist (db_events)", "read latest", "GET /signals/pca/state", "render"],
  },
  {
    id: "dash-book-health", panel: "Book health", view: "dashboard", domain: "portfolio",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Book health")],
    edges: ["UPDATE greeks", "read book", "GET /portfolio/aggregate-greeks", "render"],
  },
  {
    id: "dash-capital", panel: "Capital", view: "dashboard", domain: "portfolio",
    nodes: [eng("execution-engine", "account snaps"), DBW, pg("account_history"), API, FE, panel("Capital")],
    edges: ["account summary", "db_events", "INSERT", "latest", "GET /portfolio/account", "render"],
  },
  {
    id: "dash-today", panel: "Today — events & expiries", view: "dashboard", domain: "trade",
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Today")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
  },
  {
    id: "dash-attention", panel: "Attention (alerts)", view: "dashboard", domain: "system",
    nodes: [eng("5 engines", "heartbeat each cycle"), redis("heartbeat:<engine>"), API, FE, panel("Attention")],
    edges: ["SET heartbeat (TTL)", "read heartbeats", "GET /health/extended", "derive alerts"],
  },

  // ───────────────────────── Trade ─────────────────────────
  {
    id: "trade-indicators", panel: "Indicators", view: "trade", domain: "trade",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Indicators")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open (Σ)", "render"],
  },
  {
    id: "trade-open", panel: "Open positions", view: "trade", domain: "trade",
    nodes: [IB, IBG, eng("execution-engine", "clientId 5 · sync"), eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Open positions")],
    edges: ["positions", "reqPositions", "UPDATE greeks", "UPSERT row", "read book", "GET /positions/open", "render"],
  },
  {
    id: "trade-builder", panel: "Order builder", view: "trade", domain: "surface",
    nodes: [redis("latest_vol_surface"), API, FE, panel("Order builder")],
    edges: ["read surface", "POST /vol/trade-preview (price legs)", "preview + render"],
  },
  {
    id: "trade-close", panel: "Close position", view: "trade", domain: "trade",
    nodes: [eng("execution-engine", "RTH-aware close"), pg("booked_position · open_position"), API, FE, panel("Close position")],
    edges: ["mark closeable", "read book", "GET /positions/open · POST close", "render + arm"],
  },

  // ───────────────────────── Signal ─────────────────────────
  {
    id: "iv-surface", panel: "IV surface", view: "signals", domain: "surface",
    nodes: [IB, IBG, eng("vol-engine", "clientId 2 · 180s"), redis("latest_vol_surface · vol_surface_history"), API, FE, panel("IV surface")],
    edges: ["FOP chain", "reqMktData", "compute (SET + db_events)", "read", "GET /vol/surface", "render"],
  },
  {
    id: "mode-stability", panel: "Mode stability", view: "signals", domain: "pca",
    nodes: [eng("vol-engine", "PCA fit + project"), pg("pca_model · pca_signal_history"), API, FE, panel("Mode stability")],
    edges: ["fit/project (db_events)", "read active model", "GET /signals/pca/model", "render"],
  },
  {
    id: "fair-vol", panel: "Fair vol — level gate", view: "signals", domain: "termStructure",
    nodes: [eng("vol-engine", "YZ-RV · HAR/GARCH · VRP"), redis("latest_vol_surface"), API, FE, panel("Fair vol gate")],
    edges: ["σ_fair^Q (SET)", "read", "GET /vol/term-structure", "render"],
  },

  // ───────────────────────── Risk ─────────────────────────
  {
    id: "greeks-util", panel: "Greeks & risk utilization", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position · account_history"), API, FE, panel("Greeks & utilization")],
    edges: ["UPDATE book", "read book + account", "GET /portfolio/risk-per-tenor", "render"],
  },
  {
    id: "var", panel: "Value at Risk", view: "risk", domain: "risk",
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), DBW, pg("account_history (504d)"), API, FE, panel("Value at Risk")],
    edges: ["account summary", "reqAccountSummary", "db_events", "INSERT", "504d sim", "GET /portfolio/var", "render"],
  },
  {
    id: "marginal-var", panel: "Marginal contribution to VaR", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "per-pos pnl /2s"), pg("open_position_history"), API, FE, panel("Marginal VaR")],
    edges: ["INSERT snapshot", "daily pnl series", "GET /portfolio/marginal-var (Euler)", "render"],
  },
  {
    id: "stress", panel: "Stress test", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Stress engine")],
    edges: ["UPDATE book", "read book", "GET /portfolio/stress-grid (reval)", "render"],
  },
  {
    id: "greeks-ladder", panel: "Greeks ladder", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Greeks ladder")],
    edges: ["UPDATE book", "read book", "GET /portfolio/greeks-ladder (reval)", "render"],
  },
  {
    id: "position-breakdown", panel: "Position breakdown", view: "risk", domain: "trade",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Position breakdown")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open", "render"],
  },
  {
    id: "pin-risk", panel: "Expiries & roll-off (pin risk)", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Pin risk")],
    edges: ["UPDATE book", "read options", "GET /portfolio/pin-risk (reval)", "render"],
  },
  {
    id: "risk-macro", panel: "Macro events", view: "risk", domain: "trade",
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Macro events")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
  },

  // ───────────────────────── Portfolio ─────────────────────────
  {
    id: "account", panel: "Account & capital", view: "portfolio", domain: "portfolio",
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), DBW, pg("account_history"), API, FE, panel("Account & capital")],
    edges: ["account summary", "reqAccountSummary", "db_events", "INSERT", "latest + 24h", "GET /portfolio/account", "render"],
  },
  {
    id: "perf", panel: "Performance", view: "portfolio", domain: "portfolio",
    nodes: [eng("execution-engine", "account + close"), pg("account_history · booked_position"), API, FE, panel("Performance")],
    edges: ["INSERT", "daily series", "GET /portfolio/stats", "render"],
  },
  {
    id: "carry-convex", panel: "Carry vs convexity", view: "portfolio", domain: "portfolio",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Carry vs convexity")],
    edges: ["UPDATE Γ/Θ", "read book", "GET /portfolio/aggregate-greeks", "render"],
  },
  {
    id: "pnl-attribution", panel: "Realized P&L attribution", view: "portfolio", domain: "portfolio",
    nodes: [eng("execution-engine", "MTM /2s"), pg("booked_position_metric_history"), API, FE, panel("P&L attribution")],
    edges: ["INSERT MTM", "Taylor decomp", "GET /portfolio/pnl-attribution", "render"],
  },
  {
    id: "book-composition", panel: "Book composition", view: "portfolio", domain: "portfolio",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Book composition")],
    edges: ["UPDATE book", "read book", "GET /positions/open (grouped)", "render"],
  },
];
