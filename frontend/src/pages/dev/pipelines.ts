/**
 * Declarative end-to-end pipeline per PROD panel (dev "Pipeline" tab).
 *
 * The prod front (voldesk) has 5 tabs, each with several small panels. Each
 * panel maps to ONE full left→right schematic: source → … → api → frontend →
 * the panel itself (the terminal block). Drawn as a plumbing diagram; the
 * "current" only flows when `domain`'s data is live (proof of live) + a
 * last-update stamp. `edges.length === nodes.length - 1`.
 */
export type ViewId = "dashboard" | "trade" | "signals" | "risk" | "portfolio";

export type DomainId =
  | "surface" | "pca" | "trade" | "portfolio" | "risk" | "termStructure" | "ticks";

export type NodeKind = "external" | "container" | "store" | "api" | "frontend" | "panel";

export interface PipeNode {
  kind: NodeKind;
  label: string;
  sub?: string;
  /** Real-health source key (resolved against the system domain): an engine
   * label / stack item name (e.g. "market-data", "redis", "IB Gateway",
   * "api (FastAPI)"), or "__self" (always up) / "__ws" (the panel's WS feed).
   * Omitted → falls back to the panel's domain freshness. */
  health?: string;
}

export interface PanelPipe {
  id: string;
  panel: string;
  view: ViewId;
  domain: DomainId;
  nodes: PipeNode[]; // source → panel (terminal)
  edges: string[];
}

// reusable blocks
const IB: PipeNode = { kind: "external", label: "IB", sub: "Interactive Brokers" };
const IBG: PipeNode = { kind: "container", label: "ib-gateway", sub: "broker session" };
const API: PipeNode = { kind: "api", label: "api", sub: "FastAPI" };
const FE: PipeNode = { kind: "frontend", label: "frontend", sub: "React · fetch/WS" };
const pg = (sub: string): PipeNode => ({ kind: "store", label: "Postgres", sub });
const redis = (sub: string): PipeNode => ({ kind: "store", label: "Redis", sub });
const eng = (label: string, sub: string): PipeNode => ({ kind: "container", label, sub });
const panel = (name: string): PipeNode => ({ kind: "panel", label: name, sub: "displayed panel" });

export const PIPELINES: PanelPipe[] = [
  // ---------------- Dashboard ----------------
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
    id: "dash-signal", panel: "Active signal", view: "dashboard", domain: "pca",
    nodes: [eng("vol-engine", "PCA projection"), pg("pca_signal_history"), API, FE, panel("Active signal")],
    edges: ["persist (db_events)", "read latest", "GET /signals/pca/state", "render"],
  },
  {
    id: "dash-term", panel: "Mini term-structure", view: "dashboard", domain: "termStructure",
    nodes: [eng("vol-engine", "180s cycle"), redis("latest_vol_surface"), API, FE, panel("Term-structure mini")],
    edges: ["SET surface", "read", "GET /vol/term-structure", "render"],
  },
  {
    id: "dash-events", panel: "Today — events & expiries", view: "dashboard", domain: "trade",
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Today")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
  },

  // ---------------- Trade ----------------
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

  // ---------------- Signal ----------------
  {
    id: "iv-surface", panel: "IV surface", view: "signals", domain: "surface",
    nodes: [IB, IBG, eng("vol-engine", "clientId 2 · 180s"), redis("latest_vol_surface · vol_surface_history"), API, FE, panel("IV surface")],
    edges: ["FOP chain", "reqMktData", "compute (SET + db_events)", "read", "GET /vol/surface", "render"],
  },
  {
    id: "mode-stability", panel: "Mode stability (PCA)", view: "signals", domain: "pca",
    nodes: [eng("vol-engine", "PCA fit + project"), pg("pca_model · pca_signal_history"), API, FE, panel("Mode stability")],
    edges: ["fit/project (db_events)", "read active model", "GET /signals/pca/model", "render"],
  },
  {
    id: "fair-vol", panel: "Fair vol — level gate", view: "signals", domain: "termStructure",
    nodes: [eng("vol-engine", "YZ-RV · HAR/GARCH · VRP"), redis("latest_vol_surface"), API, FE, panel("Fair vol gate")],
    edges: ["σ_fair^Q (SET)", "read", "GET /vol/term-structure", "render"],
  },

  // ---------------- Risk ----------------
  {
    id: "var", panel: "Value at Risk", view: "risk", domain: "risk",
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), pg("account_history (504d)"), API, FE, panel("Value at Risk")],
    edges: ["account summary", "reqAccountSummary", "INSERT", "504d sim", "GET /portfolio/var", "render"],
  },
  {
    id: "stress", panel: "Stress engine", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Stress engine")],
    edges: ["UPDATE book", "read book", "GET /portfolio/stress-grid (reval)", "render"],
  },
  {
    id: "greeks-ladder", panel: "Greeks ladder", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Greeks ladder")],
    edges: ["UPDATE book", "read book", "GET /portfolio/greeks-ladder (reval)", "render"],
  },
  {
    id: "vega-pca", panel: "vega → PCA mode", view: "risk", domain: "risk",
    nodes: [eng("risk-engine + vol-engine", "greeks + PCA"), pg("open_position · pca_model"), API, FE, panel("vega → PCA")],
    edges: ["greeks + model", "read book + loadings", "GET /portfolio/vega-pca", "render"],
  },
  {
    id: "marginal-var", panel: "Marginal contribution to VaR", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "per-pos pnl /2s"), pg("open_position_history"), API, FE, panel("Marginal VaR")],
    edges: ["INSERT snapshot", "daily pnl series", "GET /portfolio/marginal-var (Euler)", "render"],
  },
  {
    id: "var-factors", panel: "VaR by factor", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("VaR by factor")],
    edges: ["UPDATE book", "read book", "GET /portfolio/var-factors (reval)", "render"],
  },
  {
    id: "greeks-util", panel: "Greeks & risk utilization", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position · account_history"), API, FE, panel("Greeks & utilization")],
    edges: ["UPDATE book", "read book + account", "GET /portfolio/risk-per-tenor", "render"],
  },
  {
    id: "pin-risk", panel: "Expiries & roll-off (pin risk)", view: "risk", domain: "risk",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Pin risk")],
    edges: ["UPDATE book", "read options", "GET /portfolio/pin-risk (reval)", "render"],
  },

  // ---------------- Portfolio ----------------
  {
    id: "account", panel: "Account & capital", view: "portfolio", domain: "portfolio",
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), pg("account_history"), API, FE, panel("Account & capital")],
    edges: ["account summary", "reqAccountSummary", "INSERT", "latest + 24h", "GET /portfolio/account", "render"],
  },
  {
    id: "equity-curve", panel: "Equity curve", view: "portfolio", domain: "portfolio",
    nodes: [eng("execution-engine", "account snaps"), pg("account_history"), API, FE, panel("Equity curve")],
    edges: ["INSERT net-liq", "windowed query", "GET /portfolio/equity-curve", "render"],
  },
  {
    id: "book-composition", panel: "Book composition", view: "portfolio", domain: "portfolio",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Book composition")],
    edges: ["UPDATE book", "read book", "GET /positions/open (grouped)", "render"],
  },
  {
    id: "carry-convex", panel: "Carry vs convexity", view: "portfolio", domain: "portfolio",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Carry vs convexity")],
    edges: ["UPDATE Γ/Θ", "read book", "GET /portfolio/aggregate-greeks", "render"],
  },
  {
    id: "daily-pnl", panel: "Daily P&L", view: "portfolio", domain: "portfolio",
    nodes: [eng("execution-engine", "close booking"), pg("booked_position (closed)"), API, FE, panel("Daily P&L")],
    edges: ["set net_pnl", "per-day sum", "GET /portfolio/daily-pnl", "render"],
  },
];
