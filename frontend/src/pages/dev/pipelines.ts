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

/**
 * Single data-flow archetype of a block (its dominant role):
 * - emit      emitter     — sends data out (source of the flow)
 * - receive   receiver    — receives / stores data (sink of the flow)
 * - transform transformer — changes the data (engines, api compute)
 * - hub       hub         — centralizes inbound + redistributes outbound (api, Redis)
 */
export type Role = "emit" | "transform" | "receive" | "hub";

export interface PipeNode {
  kind: NodeKind;
  label: string;
  sub?: string;
  health?: string;
  role?: Role;
}

export interface PanelPipe {
  id: string;
  panel: string;
  view: ViewId;
  domain: DomainId;
  nodes: PipeNode[];
  edges: string[];
  /**
   * When true, the panel carries a matching `data-pp="<id>"` in its prod view,
   * so the Pipeline terminal renders the REAL view and CSS-isolates just this
   * panel (siblings hidden). Omitted → terminal renders the whole parent view.
   */
  isolated?: boolean;
  /**
   * Accurate branching topology (sources → spine → fork into store + serve).
   * When present, the schema renders as a graph-with-a-fork instead of a flat
   * line. The flat `nodes`/`edges` stay for the sidebar health roll-up.
   */
  graph?: PipeGraph;
}

/** A horizontal chain of blocks: `edges.length === nodes.length - 1`. */
export interface GraphChain {
  nodes: PipeNode[];
  edges: string[];
}

/**
 * Branching pipeline. A `spine` runs from the source(s) through the fork node
 * (its last element — the hub). Off that hub:
 *  - `store` (optional) — where data is recorded (db-writer → Postgres). When
 *    present the serve branch forks DOWN; when absent the serve branch simply
 *    continues the spine in a straight line (live-only / read-from-DB flows).
 *  - `serve` — the read path to the panel (last node = the live terminal).
 * `sources` (optional) are extra emitters that CONVERGE into spine[0] (e.g. a
 * reval endpoint reading the book from Postgres + the surface from Redis).
 */
export interface PipeGraph {
  sources?: PipeNode[];   // converge into spine[0]
  sourceEdges?: string[]; // label per source → spine[0]
  spine: PipeNode[];
  spineEdges: string[];   // length spine.length - 1
  store?: GraphChain;     // optional store branch (fork up)
  storeEdge?: string;     // hub → store.nodes[0]
  serve: GraphChain;      // last node = the displayed panel (live terminal)
  serveEdge?: string;     // hub → serve.nodes[0]
}

// role = each block's dominant data-flow archetype (see `Role`).
const IB: PipeNode = { kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit" };
// one IB connection in, fanned out to every engine client (clientId 1/2/3/5) → hub.
const IBG: PipeNode = { kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub" };
const API: PipeNode = { kind: "api", label: "api", sub: "FastAPI", role: "hub" };
const FE: PipeNode = { kind: "frontend", label: "frontend", sub: "React · fetch/WS", role: "receive" };
const pg = (sub: string): PipeNode => ({ kind: "store", label: "Postgres", sub, role: "receive" });
const redis = (sub: string): PipeNode => ({ kind: "store", label: "Redis", sub, role: "hub" });
// engines compute — their dominant role is transformer (they never store).
const eng = (label: string, sub: string): PipeNode => ({ kind: "container", label, sub, role: "transform" });
const panel = (name: string): PipeNode => ({ kind: "panel", label: name, sub: "displayed panel", role: "receive" });
// db-writer is the only writer: it lands db_events into Postgres → receiver.
const DBW: PipeNode = { kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive" };

// ── graph nodes (carry per-block health so the pills are accurate) ──
const xIB: PipeNode = { kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" };
const xIBG: PipeNode = { kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" };
const xDBW: PipeNode = { kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive", health: "db-writer" };
const xFE: PipeNode = { kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" };
const xRedis = (sub: string): PipeNode => ({ kind: "store", label: "Redis", sub, role: "hub", health: "redis" });
const xPg = (sub: string): PipeNode => ({ kind: "store", label: "Postgres", sub, role: "receive", health: "postgres" });
const xEng = (label: string, sub: string, health: string): PipeNode => ({ kind: "container", label, sub, role: "transform", health });
const xExt = (label: string, sub: string): PipeNode => ({ kind: "external", label, sub, role: "emit" });
const xPanel = (name: string): PipeNode => ({ kind: "panel", label: name, sub: "displayed panel", role: "receive" });
const xApi = (sub: string, role: Role = "hub"): PipeNode => ({ kind: "api", label: "api", sub, role, health: "__api" });

// ── topology builders (each returns the accurate source→…→panel graph) ──
// engine computes → persisted → api serves the LIVE value from the Redis cache (FORK: store + serve)
function gFork(engine: PipeNode, redisSub: string, e0: string, e1: string, pgSub: string, apiSub: string, panelName: string, apiRole: Role = "hub"): PipeGraph {
  return {
    spine: [xIB, xIBG, engine, xRedis(redisSub)], spineEdges: [e0, e1, "SET + db_events"],
    store: { nodes: [xDBW, xPg(pgSub)], edges: ["INSERT"] }, storeEdge: "db_events",
    serve: { nodes: [xApi(apiSub, apiRole), xFE, xPanel(panelName)], edges: ["JSON", "render"] }, serveEdge: "read latest (Redis)",
  };
}
// engine computes → persisted → api READS it back from Postgres (straight line through the store)
function gPersist(engine: PipeNode, e0: string, e1: string, pgSub: string, apiSub: string, panelName: string, apiRole: Role = "hub"): PipeGraph {
  return {
    spine: [xIB, xIBG, engine, xRedis("db_events"), xDBW, xPg(pgSub)], spineEdges: [e0, e1, "publish", "consume", "INSERT"],
    serve: { nodes: [xApi(apiSub, apiRole), xFE, xPanel(panelName)], edges: ["JSON", "render"] }, serveEdge: "read",
  };
}
// live-only, never persisted (ticks) — straight line, no store
function gLive(engine: PipeNode, redisSub: string, e0: string, e1: string, apiSub: string, panelName: string): PipeGraph {
  return {
    spine: [xIB, xIBG, engine, xRedis(redisSub)], spineEdges: [e0, e1, "publish"],
    serve: { nodes: [xApi(apiSub), xFE, xPanel(panelName)], edges: ["WS", "render"] }, serveEdge: "WS bridge",
  };
}
// computed on demand by the api from several converging sources (no engine, no store)
function gReval(sources: PipeNode[], sourceEdges: string[], apiSub: string, panelName: string): PipeGraph {
  return {
    sources, sourceEdges, spine: [xApi(apiSub, "transform")], spineEdges: [],
    serve: { nodes: [xFE, xPanel(panelName)], edges: ["render"] }, serveEdge: "JSON",
  };
}
// macro events: external providers → api scheduler → Postgres → api serve
function gEvents(panelName: string): PipeGraph {
  return {
    spine: [xExt("macro providers", "FRED · ECB · BoE · FOMC"), { kind: "api", label: "events scheduler", sub: "fetch + dedup", role: "transform", health: "__api" }, xPg("event_calendar")],
    spineEdges: ["fetch", "upsert"],
    serve: { nodes: [xApi("GET /regime/events"), xFE, xPanel(panelName)], edges: ["JSON", "render"] }, serveEdge: "read",
  };
}

export const PIPELINES: PanelPipe[] = [
  // ───────────────────────── Dashboard ─────────────────────────
  {
    id: "ticker", panel: "Spot ticker (bid/ask)", view: "dashboard", domain: "ticks",
    nodes: [
      { kind: "external", label: "IB", sub: "Interactive Brokers", health: "IB Gateway", role: "emit" },
      { kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", health: "IB Gateway", role: "hub" },
      { kind: "container", label: "market-data", sub: "clientId 1 · tick stream", health: "market-data", role: "transform" },
      { kind: "store", label: "Redis", sub: "latest_spot:EUR · ticks ch.", health: "redis", role: "hub" },
      { kind: "api", label: "api", sub: "FastAPI · WS bridge", health: "__api", role: "hub" },
      { kind: "frontend", label: "frontend", sub: "React · WS client", health: "__self", role: "receive" },
      { kind: "panel", label: "Ticker bid/ask", sub: "displayed panel", health: "__ws", role: "receive" },
    ],
    edges: ["get tick", "reqMktData", "publish ticks", "WS bridge", "WS /ws/ticks", "render"],
    graph: gLive(xEng("market-data", "clientId 1 · tick stream", "market-data"), "latest_spot:EUR · ticks ch.", "get tick", "reqMktData", "FastAPI · WS bridge", "Ticker bid/ask"),
  },
  {
    id: "dash-market", panel: "Market snapshot", view: "dashboard", domain: "ticks", isolated: true,
    nodes: [IB, IBG, eng("market-data", "clientId 1"), redis("latest_spot:EUR"), API, FE, panel("Market snapshot")],
    edges: ["get tick", "reqMktData", "publish", "WS bridge", "WS /ws/ticks", "render"],
    graph: gLive(xEng("market-data", "clientId 1", "market-data"), "latest_spot:EUR", "get tick", "reqMktData", "FastAPI · WS bridge", "Market snapshot"),
  },
  {
    id: "dash-signal", panel: "Active signal", view: "dashboard", domain: "pca", isolated: true,
    nodes: [eng("vol-engine", "PCA projection"), pg("pca_signal_history"), API, FE, panel("Active signal")],
    edges: ["persist (db_events)", "read latest", "GET /signals/pca/state", "render"],
    graph: gPersist(xEng("vol-engine", "PCA project", "vol-engine"), "FOP chain", "reqMktData", "pca_signal_history", "GET /signals/pca/state", "Active signal"),
  },
  {
    id: "dash-book-health", panel: "Book health", view: "dashboard", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Book health")],
    edges: ["UPDATE greeks", "read book", "GET /portfolio/aggregate-greeks", "render"],
    graph: gPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE greeks", "open_position", "GET /portfolio/aggregate-greeks", "Book health"),
  },
  {
    id: "dash-capital", panel: "Capital", view: "dashboard", domain: "portfolio", isolated: true,
    nodes: [eng("execution-engine", "account snaps"), DBW, pg("account_history"), API, FE, panel("Capital")],
    edges: ["account summary", "db_events", "INSERT", "latest", "GET /portfolio/account", "render"],
    graph: gPersist(xEng("execution-engine", "account snaps", "exec-engine"), "account summary", "publish", "account_history", "GET /portfolio/account", "Capital"),
  },
  {
    id: "dash-today", panel: "Today — events & expiries", view: "dashboard", domain: "trade", isolated: true,
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Today")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
    graph: gEvents("Today"),
  },
  {
    id: "dash-attention", panel: "Attention (alerts)", view: "dashboard", domain: "system", isolated: true,
    nodes: [eng("5 engines", "heartbeat each cycle"), redis("heartbeat:<engine>"), API, FE, panel("Attention")],
    edges: ["SET heartbeat (TTL)", "read heartbeats", "GET /health/extended", "derive alerts"],
    graph: {
      spine: [{ kind: "container", label: "5 engines", sub: "heartbeat each cycle", role: "emit" }, xRedis("heartbeat:<engine> (TTL)")],
      spineEdges: ["SET heartbeat"],
      serve: { nodes: [xApi("GET /health/extended"), xFE, xPanel("Attention")], edges: ["JSON", "derive alerts"] }, serveEdge: "read heartbeats",
    },
  },

  // ───────────────────────── Trade ─────────────────────────
  {
    id: "trade-indicators", panel: "Indicators", view: "trade", domain: "trade", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Indicators")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open (Σ)", "render"],
    graph: gPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE greeks", "open_position", "GET /positions/open (Σ)", "Indicators"),
  },
  {
    id: "trade-open", panel: "Open positions", view: "trade", domain: "trade", isolated: true,
    nodes: [IB, IBG, eng("execution-engine", "clientId 5 · sync"), eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Open positions")],
    edges: ["positions", "reqPositions", "UPDATE greeks", "UPSERT row", "read book", "GET /positions/open", "render"],
    graph: gPersist(xEng("execution-engine", "clientId 5 · sync", "exec-engine"), "positions", "UPSERT row", "open_position", "GET /positions/open", "Open positions"),
  },
  {
    id: "trade-builder", panel: "Order builder", view: "trade", domain: "surface", isolated: true,
    nodes: [redis("latest_vol_surface"), API, FE, panel("Order builder")],
    edges: ["read surface", "POST /vol/trade-preview (price legs)", "preview + render"],
    graph: {
      spine: [xRedis("latest_vol_surface"), xApi("POST /vol/trade-preview · price legs", "transform")],
      spineEdges: ["read surface"],
      serve: { nodes: [xFE, xPanel("Order builder")], edges: ["render"] }, serveEdge: "preview",
    },
  },
  {
    id: "trade-close", panel: "Close position", view: "trade", domain: "trade", isolated: true,
    nodes: [eng("execution-engine", "RTH-aware close"), pg("booked_position · open_position"), API, FE, panel("Close position")],
    edges: ["mark closeable", "read book", "GET /positions/open · POST close", "render + arm"],
    graph: gPersist(xEng("execution-engine", "RTH-aware close", "exec-engine"), "positions", "mark closeable", "booked_position · open_position", "GET /positions/open · POST close", "Close position"),
  },

  // ───────────────────────── Signal ─────────────────────────
  {
    id: "iv-surface", panel: "IV surface", view: "signals", domain: "surface", isolated: true,
    nodes: [IB, IBG, eng("vol-engine", "clientId 2 · 180s"), redis("latest_vol_surface · vol_surface_history"), API, FE, panel("IV surface")],
    edges: ["FOP chain", "reqMktData", "compute (SET + db_events)", "read", "GET /vol/surface", "render"],
    graph: {
      spine: [
        { kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { kind: "container", label: "vol-engine", sub: "clientId 2 · 180s cycle", role: "transform", health: "vol-engine" },
        { kind: "store", label: "Redis", sub: "SET latest_vol_surface + db_events", role: "hub", health: "redis" },
      ],
      spineEdges: ["FOP chain", "reqMktData", "SVI/SSVI calibrate"],
      storeEdge: "db_events",
      store: {
        nodes: [
          { kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive", health: "db-writer" },
          { kind: "store", label: "Postgres", sub: "vol_surface_history", role: "receive", health: "postgres" },
        ],
        edges: ["INSERT row"],
      },
      serveEdge: "read latest",
      serve: {
        nodes: [
          { kind: "api", label: "api", sub: "FastAPI · GET /vol/surface", role: "hub", health: "__api" },
          { kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
          { kind: "panel", label: "IV surface", sub: "displayed panel", role: "receive" },
        ],
        edges: ["JSON surface", "render"],
      },
    },
  },
  {
    id: "mode-stability", panel: "Mode stability", view: "signals", domain: "pca", isolated: true,
    nodes: [eng("vol-engine", "PCA fit + project"), pg("pca_model · pca_signal_history"), API, FE, panel("Mode stability")],
    edges: ["fit/project (db_events)", "read active model", "GET /signals/pca/model", "render"],
    graph: gPersist(xEng("vol-engine", "PCA fit + project", "vol-engine"), "FOP chain", "reqMktData", "pca_model · pca_signal_history", "GET /signals/pca/model", "Mode stability"),
  },
  {
    id: "fair-vol", panel: "Fair vol — level gate", view: "signals", domain: "termStructure", isolated: true,
    nodes: [eng("vol-engine", "YZ-RV · HAR/GARCH · VRP"), redis("latest_vol_surface"), API, FE, panel("Fair vol gate")],
    edges: ["σ_fair^Q (SET)", "read", "GET /vol/term-structure", "render"],
    graph: gFork(xEng("vol-engine", "YZ-RV · HAR/GARCH · VRP", "vol-engine"), "latest_vol_surface (σ_fair)", "FOP chain", "reqMktData", "vol_surface_history", "GET /vol/term-structure", "Fair vol gate"),
  },
  {
    id: "pca-modes", panel: "PCA engine — surface modes", view: "signals", domain: "pca", isolated: true,
    nodes: [eng("vol-engine", "PCA fit + project"), pg("pca_model · pca_signal_history"), API, FE, panel("PCA modes")],
    edges: ["fit/project (db_events)", "read pcs", "GET /signals/pca/model", "render"],
    graph: gPersist(xEng("vol-engine", "PCA fit + project", "vol-engine"), "FOP chain", "reqMktData", "pca_model · pca_signal_history", "GET /signals/pca/model", "PCA modes"),
  },

  // ───────────────────────── Risk ─────────────────────────
  {
    id: "greeks-util", panel: "Greeks & risk utilization", view: "risk", domain: "risk", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position · account_history"), API, FE, panel("Greeks & utilization")],
    edges: ["UPDATE book", "read book + account", "GET /portfolio/risk-per-tenor", "render"],
    graph: gPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE book", "open_position · account_history", "GET /portfolio/risk-per-tenor", "Greeks & utilization"),
  },
  {
    id: "var", panel: "Value at Risk", view: "risk", domain: "risk", isolated: true,
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), DBW, pg("account_history (504d)"), API, FE, panel("Value at Risk")],
    edges: ["account summary", "reqAccountSummary", "db_events", "INSERT", "504d sim", "GET /portfolio/var", "render"],
    graph: gPersist(xEng("execution-engine", "account snaps", "exec-engine"), "account summary", "publish", "account_history (504d)", "GET /portfolio/var · sim", "Value at Risk", "transform"),
  },
  {
    id: "marginal-var", panel: "Marginal contribution to VaR", view: "risk", domain: "risk", isolated: true,
    nodes: [eng("risk-engine", "per-pos pnl /2s"), pg("open_position_history"), API, FE, panel("Marginal VaR")],
    edges: ["INSERT snapshot", "daily pnl series", "GET /portfolio/marginal-var (Euler)", "render"],
    graph: gPersist(xEng("risk-engine", "per-pos pnl /2s", "risk-engine"), "positions", "INSERT snapshot", "open_position_history", "GET /portfolio/marginal-var · Euler", "Marginal VaR", "transform"),
  },
  {
    id: "stress", panel: "Stress test", view: "risk", domain: "risk", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Stress engine")],
    edges: ["UPDATE book", "read book", "GET /portfolio/stress-grid (reval)", "render"],
    graph: gReval([xPg("open_position"), xRedis("latest_vol_surface")], ["read book", "read surface"], "GET /portfolio/stress-grid · reval_book", "Stress engine"),
  },
  {
    id: "greeks-ladder", panel: "Greeks ladder", view: "risk", domain: "risk", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Greeks ladder")],
    edges: ["UPDATE book", "read book", "GET /portfolio/greeks-ladder (reval)", "render"],
    graph: gReval([xPg("open_position"), xRedis("latest_vol_surface")], ["read book", "read surface"], "GET /portfolio/greeks-ladder · reval", "Greeks ladder"),
  },
  {
    id: "position-breakdown", panel: "Position breakdown", view: "risk", domain: "trade", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Position breakdown")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open", "render"],
    graph: gPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE greeks", "open_position", "GET /positions/open", "Position breakdown"),
  },
  {
    id: "pin-risk", panel: "Expiries & roll-off (pin risk)", view: "risk", domain: "risk", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Pin risk")],
    edges: ["UPDATE book", "read options", "GET /portfolio/pin-risk (reval)", "render"],
    graph: gReval([xPg("open_position"), xRedis("latest_vol_surface")], ["read options", "read surface"], "GET /portfolio/pin-risk · reval", "Pin risk"),
  },
  {
    id: "risk-macro", panel: "Macro events", view: "risk", domain: "trade", isolated: true,
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Macro events")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
    graph: gEvents("Macro events"),
  },

  // ───────────────────────── Portfolio ─────────────────────────
  {
    id: "account", panel: "Account & capital", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), DBW, pg("account_history"), API, FE, panel("Account & capital")],
    edges: ["account summary", "reqAccountSummary", "db_events", "INSERT", "latest + 24h", "GET /portfolio/account", "render"],
    graph: gPersist(xEng("execution-engine", "account snaps", "exec-engine"), "account summary", "publish", "account_history", "GET /portfolio/account", "Account & capital"),
  },
  {
    id: "perf", panel: "Performance", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("execution-engine", "account + close"), pg("account_history · booked_position"), API, FE, panel("Performance")],
    edges: ["INSERT", "daily series", "GET /portfolio/stats", "render"],
    graph: gPersist(xEng("execution-engine", "account + close", "exec-engine"), "account summary", "publish", "account_history · booked_position", "GET /portfolio/stats", "Performance", "transform"),
  },
  {
    id: "carry-convex", panel: "Carry vs convexity", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Carry vs convexity")],
    edges: ["UPDATE Γ/Θ", "read book", "GET /portfolio/aggregate-greeks", "render"],
    graph: gPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE Γ/Θ", "open_position", "GET /portfolio/aggregate-greeks", "Carry vs convexity"),
  },
  {
    id: "pnl-attribution", panel: "Realized P&L attribution", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("execution-engine", "MTM /2s"), pg("booked_position_metric_history"), API, FE, panel("P&L attribution")],
    edges: ["INSERT MTM", "Taylor decomp", "GET /portfolio/pnl-attribution", "render"],
    graph: gPersist(xEng("execution-engine", "MTM /2s", "exec-engine"), "fills", "INSERT MTM", "booked_position_metric_history", "GET /portfolio/pnl-attribution · Taylor", "P&L attribution", "transform"),
  },
  {
    id: "book-composition", panel: "Book composition", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Book composition")],
    edges: ["UPDATE book", "read book", "GET /positions/open (grouped)", "render"],
    graph: gPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE book", "open_position", "GET /positions/open (grouped)", "Book composition"),
  },
];
