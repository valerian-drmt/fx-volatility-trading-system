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
   * Full data-flow DAG (dagre-laid-out): every real input/output, including
   * shared dual-role nodes (e.g. Postgres written-by db-writer AND read-by the
   * api). The flat `nodes`/`edges` stay for the sidebar health roll-up.
   */
  dag?: PipeDag;
  /**
   * Per-panel refresh cadence shown in the schema header. Overrides the coarse
   * per-domain default — needed where a panel polls on its own (stress/ladders/
   * marginal/pin ~120s) or its data changes on a slow schedule (macro ~24h)
   * even though the front re-reads it on the domain beat.
   */
  cadence?: string;
}

/** A node in the full data-flow DAG. `terminal` = the displayed panel. */
export interface DagNode {
  id: string;
  kind: NodeKind;
  label: string;
  sub?: string;
  role: Role;
  health?: string;
  terminal?: boolean;
}

/** A directed data edge `from → to` with an optional flow label. */
export interface DagEdge {
  from: string;
  to: string;
  label?: string;
}

export interface PipeDag {
  nodes: DagNode[];
  edges: DagEdge[];
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

// ── DAG node helpers ──
const toDag = (n: PipeNode, id: string): DagNode => ({
  id, kind: n.kind, label: n.label, role: n.role ?? "receive",
  ...(n.sub !== undefined ? { sub: n.sub } : {}),
  ...(n.health !== undefined ? { health: n.health } : {}),
});
const dApi = (sub: string, role: Role = "hub"): DagNode => ({ id: "api", kind: "api", label: "api", sub, role, health: "__api" });
const dPanel = (name: string): DagNode => ({ id: "panel", kind: "panel", label: name, sub: "displayed panel", role: "receive", terminal: true });

// ── DAG topology builders (same call sites as the old tree builders) ──
// engine → persisted → api READS it back from Postgres (linear; Postgres in+out = dual role).
function dagPersist(engine: PipeNode, e0: string, e1: string, pgSub: string, apiSub: string, panelName: string, apiRole: Role = "hub"): PipeDag {
  return {
    nodes: [toDag(xIB, "ib"), toDag(xIBG, "ibg"), toDag(engine, "eng"), toDag(xRedis("db_events"), "redis"), toDag(xDBW, "dbw"), toDag(xPg(pgSub), "pg"), dApi(apiSub, apiRole), toDag(xFE, "fe"), dPanel(panelName)],
    edges: [
      { from: "ib", to: "ibg", label: e0 }, { from: "ibg", to: "eng", label: e1 },
      { from: "eng", to: "redis", label: "publish" }, { from: "redis", to: "dbw", label: "db_events" },
      { from: "dbw", to: "pg", label: "INSERT" }, { from: "pg", to: "api", label: "read" },
      { from: "api", to: "fe", label: "JSON" }, { from: "fe", to: "panel", label: "render" },
    ],
  };
}
// live-only, never persisted (ticks): IB → … → Redis → api (no store branch).
function dagLive(engine: PipeNode, redisSub: string, e0: string, e1: string, apiSub: string, panelName: string): PipeDag {
  return {
    nodes: [toDag(xIB, "ib"), toDag(xIBG, "ibg"), toDag(engine, "eng"), toDag(xRedis(redisSub), "redis"), dApi(apiSub), toDag(xFE, "fe"), dPanel(panelName)],
    edges: [
      { from: "ib", to: "ibg", label: e0 }, { from: "ibg", to: "eng", label: e1 },
      { from: "eng", to: "redis", label: "publish" }, { from: "redis", to: "api", label: "WS bridge" },
      { from: "api", to: "fe", label: "JSON" }, { from: "fe", to: "panel", label: "render" },
    ],
  };
}
// macro events: providers → scheduler → Postgres → api serve (Postgres dual-role).
function dagEvents(panelName: string): PipeDag {
  return {
    nodes: [
      { id: "ext", kind: "external", label: "macro providers", sub: "FRED · ECB · BoE · FOMC", role: "emit" },
      { id: "sched", kind: "api", label: "events scheduler", sub: "fetch + dedup", role: "transform", health: "__api" },
      toDag(xPg("event_calendar"), "pg"), dApi("GET /regime/events"), toDag(xFE, "fe"), dPanel(panelName),
    ],
    edges: [
      { from: "ext", to: "sched", label: "fetch" }, { from: "sched", to: "pg", label: "upsert" },
      { from: "pg", to: "api", label: "read" }, { from: "api", to: "fe", label: "JSON" }, { from: "fe", to: "panel", label: "render" },
    ],
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
    dag: dagLive(xEng("market-data", "clientId 1 · tick stream", "market-data"), "latest_spot:EUR · ticks ch.", "get tick", "reqMktData", "FastAPI · WS bridge", "Ticker bid/ask"),
  },
  {
    id: "dash-market", panel: "Market snapshot", view: "dashboard", domain: "ticks", isolated: true,
    nodes: [IB, IBG, eng("market-data", "clientId 1"), redis("latest_spot:EUR"), API, FE, panel("Market snapshot")],
    edges: ["get tick", "reqMktData", "publish", "WS bridge", "WS /ws/ticks", "render"],
    dag: dagLive(xEng("market-data", "clientId 1", "market-data"), "latest_spot:EUR", "get tick", "reqMktData", "FastAPI · WS bridge", "Market snapshot"),
  },
  {
    id: "dash-signal", panel: "Active signal", view: "dashboard", domain: "pca", isolated: true,
    nodes: [eng("vol-engine", "PCA projection"), pg("pca_signal_history"), API, FE, panel("Active signal")],
    edges: ["persist (db_events)", "read latest", "GET /signals/pca/state", "render"],
    dag: dagPersist(xEng("vol-engine", "PCA project", "vol-engine"), "FOP chain", "reqMktData", "pca_signal_history", "GET /signals/pca/state", "Active signal"),
  },
  {
    id: "dash-book-health", panel: "Book health", view: "dashboard", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Book health")],
    edges: ["UPDATE greeks", "read book", "GET /portfolio/aggregate-greeks", "render"],
    dag: dagPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE greeks", "open_position", "GET /portfolio/aggregate-greeks", "Book health"),
  },
  {
    id: "dash-capital", panel: "Capital", view: "dashboard", domain: "portfolio", isolated: true,
    nodes: [eng("execution-engine", "account snaps"), DBW, pg("account_history"), API, FE, panel("Capital")],
    edges: ["account summary", "db_events", "INSERT", "latest", "GET /portfolio/account", "render"],
    dag: dagPersist(xEng("execution-engine", "account snaps", "exec-engine"), "account summary", "publish", "account_history", "GET /portfolio/account", "Capital"),
  },
  {
    id: "dash-today", panel: "Today — events & expiries", view: "dashboard", domain: "trade", isolated: true,
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Today")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
    dag: dagEvents("Today"),
  },
  {
    id: "dash-attention", panel: "Attention (alerts)", view: "dashboard", domain: "system", isolated: true,
    nodes: [eng("5 engines", "heartbeat each cycle"), redis("heartbeat:<engine>"), API, FE, panel("Attention")],
    edges: ["SET heartbeat (TTL)", "read heartbeats", "GET /health/extended", "derive alerts"],
    dag: {
      nodes: [
        { id: "engs", kind: "container", label: "5 engines", sub: "heartbeat each cycle", role: "emit" },
        { id: "redis", kind: "store", label: "Redis", sub: "heartbeat:<engine> (TTL)", role: "hub", health: "redis" },
        { id: "api", kind: "api", label: "api", sub: "GET /health/extended", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Attention", sub: "displayed panel", role: "receive", terminal: true },
      ],
      edges: [
        { from: "engs", to: "redis", label: "SET heartbeat" },
        { from: "redis", to: "api", label: "read heartbeats" },
        { from: "api", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "derive alerts" },
      ],
    },
  },

  // ───────────────────────── Trade ─────────────────────────
  {
    id: "trade-indicators", panel: "Indicators", view: "trade", domain: "trade", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Indicators")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open (Σ)", "render"],
    dag: dagPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE greeks", "open_position", "GET /positions/open (Σ)", "Indicators"),
  },
  {
    id: "trade-open", panel: "Open positions", view: "trade", domain: "trade", isolated: true,
    nodes: [IB, IBG, eng("execution-engine", "clientId 5 · sync"), eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Open positions")],
    edges: ["positions", "reqPositions", "UPDATE greeks", "UPSERT row", "read book", "GET /positions/open", "render"],
    dag: dagPersist(xEng("execution-engine", "clientId 5 · sync", "exec-engine"), "positions", "UPSERT row", "open_position", "GET /positions/open", "Open positions"),
  },
  {
    id: "trade-builder", panel: "Order builder", view: "trade", domain: "surface", isolated: true,
    nodes: [redis("latest_vol_surface"), API, FE, panel("Order builder")],
    edges: ["read surface", "POST /vol/trade-preview (price legs)", "preview + render"],
    dag: {
      nodes: [
        { id: "redis", kind: "store", label: "Redis", sub: "latest_vol_surface", role: "hub", health: "redis" },
        { id: "api", kind: "api", label: "api", sub: "POST /vol/trade-preview · price legs", role: "transform", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Order builder", sub: "displayed panel", role: "receive", terminal: true },
      ],
      edges: [
        { from: "redis", to: "api", label: "read surface" },
        { from: "api", to: "fe", label: "preview" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "trade-close", panel: "Close position", view: "trade", domain: "trade", isolated: true,
    nodes: [eng("execution-engine", "RTH-aware close"), pg("booked_position · open_position"), API, FE, panel("Close position")],
    edges: ["mark closeable", "read book", "GET /positions/open · POST close", "render + arm"],
    dag: dagPersist(xEng("execution-engine", "RTH-aware close", "exec-engine"), "positions", "mark closeable", "booked_position · open_position", "GET /positions/open · POST close", "Close position"),
  },

  // ───────────────────────── Signal ─────────────────────────
  {
    id: "iv-surface", panel: "IV surface", view: "signals", domain: "surface", isolated: true,
    nodes: [IB, IBG, eng("vol-engine", "clientId 2 · 180s"), redis("latest_vol_surface · vol_surface_history"), API, FE, panel("IV surface")],
    edges: ["FOP chain", "reqMktData", "compute (SET + db_events)", "read", "GET /vol/surface", "render"],
    // Decomposed surface build: listed FOP chain → per-tenor SVI calibration →
    // joint SSVI smoothing → PCHIP delta-pillar smile (SVI fallback) → display
    // grid; cross_sectional_z attaches a per-cell shape z (iv vs current-surface
    // mean/std — heatmap colour) live, no history needed.
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "chain", kind: "container", label: "FOP chain", sub: "vol-engine · chain_fetcher · listed strikes/tenor", role: "transform", health: "vol-engine" },
        { id: "svi", kind: "container", label: "SVI fit", sub: "vol-engine · per-tenor w(k)=a+b(ρ(k−m)+√…)", role: "transform", health: "vol-engine" },
        { id: "ssvi", kind: "container", label: "SSVI surface", sub: "vol-engine · joint (η,γ,ρ) w(k,θ)", role: "transform", health: "vol-engine" },
        { id: "pchip", kind: "container", label: "PCHIP smile", sub: "vol-engine · monotone Δ pillars (SVI fallback)", role: "transform", health: "vol-engine" },
        { id: "zcell", kind: "container", label: "cross-sectional z", sub: "vol-engine · z=(iv−mean)/std per cell", role: "transform", health: "vol-engine" },
        { id: "redis", kind: "store", label: "Redis", sub: "SET latest_vol_surface + db_events", role: "hub", health: "redis" },
        { id: "dbw", kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive", health: "db-writer" },
        { id: "pg", kind: "store", label: "Postgres", sub: "vol_surface_history", role: "receive", health: "postgres" },
        { id: "api", kind: "api", label: "api", sub: "FastAPI · GET /vol/surface", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "IV surface", sub: "6×5 grid · z heatmap", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "FOP chain" },
        { from: "ibg", to: "chain", label: "reqContractDetails + reqMktData" },
        { from: "chain", to: "svi", label: "listed (K, iv)" },
        { from: "svi", to: "ssvi", label: "SviParams /tenor" },
        { from: "ssvi", to: "pchip", label: "smoothed surface" },
        { from: "pchip", to: "zcell", label: "6×5 display pillars" },
        { from: "zcell", to: "redis", label: "grid + per-cell z (SET + db_events)" },
        { from: "redis", to: "dbw", label: "db_events" },
        { from: "dbw", to: "pg", label: "INSERT" },
        { from: "redis", to: "api", label: "read latest + z (live)" },
        { from: "pg", to: "api", label: "history (surface_at)" },
        { from: "api", to: "fe", label: "GET /vol/surface" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "fair-vol", panel: "Fair vol", view: "signals", domain: "termStructure", isolated: true,
    nodes: [eng("vol-engine", "YZ-RV · HAR/GARCH · VRP"), redis("latest_vol_surface"), API, FE, panel("Fair vol")],
    edges: ["σ_fair^Q (SET)", "read", "GET /vol/term-structure", "render"],
    // Decomposed σ_fair^Q math (build_fair_q): per-tenor Yang-Zhang RV is the
    // preferred σ^P; HAR-RV / GARCH(1,1) are fallbacks; regime comes from the
    // live ATM level + 1M↔6M slope; VRP(tenor,regime) is added → σ_fair^Q.
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "vol", kind: "container", label: "vol-engine", sub: "clientId 2 · live IV pillars (ATM/BF/RR)", role: "transform", health: "vol-engine" },
        { id: "ohlc", kind: "store", label: "OHLC daily", sub: "historical_fetcher · cached", role: "hub", health: "vol-engine" },
        { id: "yz", kind: "container", label: "Yang-Zhang RV", sub: "vol-engine · per-tenor σ^P (preferred)", role: "transform", health: "vol-engine" },
        { id: "har", kind: "container", label: "HAR-RV", sub: "vol-engine · σ^P fallback (d/w/m OLS)", role: "transform", health: "vol-engine" },
        { id: "garch", kind: "container", label: "GARCH(1,1)", sub: "vol-engine · σ^P fallback (arch MLE)", role: "transform", health: "vol-engine" },
        { id: "vrp", kind: "container", label: "VRP curve", sub: "vol-engine · by tenor × regime", role: "transform", health: "vol-engine" },
        { id: "fairq", kind: "container", label: "build_fair_q", sub: "vol-engine · σ_fair^Q = σ^P + VRP", role: "transform", health: "vol-engine" },
        { id: "redis", kind: "store", label: "Redis", sub: "latest_vol_surface (σ_fair^Q)", role: "hub", health: "redis" },
        { id: "api", kind: "api", label: "api", sub: "GET /vol/term-structure", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Fair vol", sub: "curves + table", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "FOP chain" },
        { from: "ibg", to: "vol", label: "reqMktData" },
        { from: "ibg", to: "ohlc", label: "reqHistoricalData (daily)" },
        { from: "ohlc", to: "yz", label: "daily OHLC" },
        { from: "ohlc", to: "har", label: "daily OHLC" },
        { from: "ohlc", to: "garch", label: "daily OHLC" },
        { from: "yz", to: "fairq", label: "σ^P (rv_tenor)" },
        { from: "har", to: "fairq", label: "σ^P fallback" },
        { from: "garch", to: "fairq", label: "σ^P fallback" },
        { from: "vol", to: "fairq", label: "ATM + slope → regime" },
        { from: "vrp", to: "fairq", label: "+VRP pts" },
        { from: "fairq", to: "redis", label: "σ_fair^Q SET" },
        { from: "redis", to: "api", label: "read" },
        { from: "api", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "pca-modes", panel: "PCA engine — surface modes", view: "signals", domain: "pca", isolated: true,
    cadence: "~3 min read · refit weekly (≥6 snaps)",
    nodes: [eng("vol-engine", "snapshot + project"), DBW, pg("snapshot → model → signal"), API, FE, panel("PCA modes")],
    edges: ["snap + z/label (db_events)", "INSERT", "read model + signals", "GET /signals/pca/*", "render"],
    // Decomposed PCA path: ① hourly 30-dim snapshot accumulates; ② weekly SVD
    // refit (api scheduler, ≥6 snaps) → means/stds/loadings; ③ per-cycle the
    // vol-engine projects the live surface (raw = loadings·x_std), z-scores it
    // vs history, and labels CHEAP/FAIR/EXPENSIVE through the 7 actionable gates.
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "vol", kind: "container", label: "vol-engine", sub: "clientId 2 · surface → 30-dim x", role: "transform", health: "vol-engine" },
        { id: "redis", kind: "store", label: "Redis", sub: "db_events", role: "hub", health: "redis" },
        { id: "dbw", kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive", health: "db-writer" },
        { id: "pgsnap", kind: "store", label: "Postgres", sub: "pca_surface_snapshot_history (hourly)", role: "receive", health: "postgres" },
        { id: "fit", kind: "api", label: "PCA refit · SVD", sub: "api scheduler · ≥6 snaps → loadings/eigvals", role: "transform", health: "__api" },
        { id: "pgmodel", kind: "store", label: "Postgres", sub: "pca_model (loadings · variance)", role: "receive", health: "postgres" },
        { id: "proj", kind: "container", label: "project", sub: "vol-engine · raw = loadings · x_std", role: "transform", health: "vol-engine" },
        { id: "zlabel", kind: "container", label: "z + label", sub: "vol-engine · z vs hist → CHEAP/FAIR/EXP · 7 gates", role: "transform", health: "vol-engine" },
        { id: "pgsig", kind: "store", label: "Postgres", sub: "pca_signal_history (z_score · label)", role: "receive", health: "postgres" },
        { id: "api", kind: "api", label: "api", sub: "GET /signals/pca/state · /history · /model", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "PCA modes", sub: "cards · z-history · loadings", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "FOP chain" },
        { from: "ibg", to: "vol", label: "reqMktData" },
        { from: "vol", to: "redis", label: "hourly snap (db_events)" },
        { from: "redis", to: "dbw", label: "db_events" },
        { from: "dbw", to: "pgsnap", label: "INSERT hourly" },
        { from: "pgsnap", to: "fit", label: "read ≥6 snaps" },
        { from: "fit", to: "pgmodel", label: "INSERT model (weekly)" },
        { from: "pgmodel", to: "proj", label: "active loadings" },
        { from: "vol", to: "proj", label: "current surface x" },
        { from: "proj", to: "zlabel", label: "raw_score" },
        { from: "zlabel", to: "pgsig", label: "INSERT z/label · per cycle" },
        { from: "pgsnap", to: "api", label: "snapshot count" },
        { from: "pgmodel", to: "api", label: "variance · loadings_grid" },
        { from: "pgsig", to: "api", label: "z_score · label" },
        { from: "api", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },

  // ───────────────────────── Risk ─────────────────────────
  // One pipeline per DATA PATH (endpoint), each with a maximally-decomposed DAG:
  // every genuine compute step (VaR sim, Euler allocation, per-axis reval, greek
  // limits, per-leg BS) is its own node. Flat nodes/edges stay for the sidebar.
  {
    id: "var", panel: "VaR", view: "risk", domain: "risk", isolated: true,
    cadence: "~60s poll · historical 1d (~504 sessions)",
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), DBW, pg("account_history (504d)"), API, FE, panel("VaR")],
    edges: ["account summary", "reqAccountSummary", "db_events", "INSERT", "504d sim", "GET /portfolio/var", "render"],
    // account_history is written DIRECTLY by the execution-engine (db.add/commit),
    // not via Redis/db-writer. VaR = empirical historical simulation on daily Δnet-liq.
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "exec", kind: "container", label: "execution-engine", sub: "clientId 5 · account_summary() snapshot", role: "transform", health: "exec-engine" },
        { id: "pgacct", kind: "store", label: "Postgres", sub: "account_history (net_liq_usd · direct INSERT)", role: "receive", health: "postgres" },
        { id: "cfg", kind: "store", label: "Postgres", sub: "config_scalar 'portfolio' (lookback · max_gap)", role: "receive", health: "postgres" },
        { id: "daily", kind: "api", label: "daily net-liq", sub: "api · DISTINCT ON (day) net_liq · ~504d", role: "transform", health: "__api" },
        { id: "deltas", kind: "api", label: "1d deltas", sub: "api · consecutive-day Δnet_liq · gap ≤ max_gap", role: "transform", health: "__api" },
        { id: "sort", kind: "api", label: "sort + percentile", sub: "api · _percentile(0.05 / 0.01) interp", role: "transform", health: "__api" },
        { id: "varstat", kind: "api", label: "VaR 95 / 99", sub: "api · _var_stats quantile (≥5d)", role: "transform", health: "__api" },
        { id: "es", kind: "api", label: "ES 99", sub: "api · mean of losses ≤ VaR99 tail", role: "transform", health: "__api" },
        { id: "mean", kind: "api", label: "mean daily P&L", sub: "api · Σdeltas / n → exp. return", role: "transform", health: "__api" },
        { id: "hist", kind: "api", label: "histogram", sub: "api · _histogram Sturges bins (5..21)", role: "transform", health: "__api" },
        { id: "api", kind: "api", label: "api", sub: "GET /portfolio/var", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch + /risk-per-tenor (√t)", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "VaR", sub: "horizon table + empirical P&L histogram", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "account updates" },
        { from: "ibg", to: "exec", label: "reqAccountSummary" },
        { from: "exec", to: "pgacct", label: "INSERT snapshot (~30s)" },
        { from: "pgacct", to: "daily", label: "read net_liq ~504d" },
        { from: "cfg", to: "daily", label: "var_lookback_days" },
        { from: "daily", to: "deltas", label: "day-keyed series" },
        { from: "cfg", to: "deltas", label: "var_max_gap_days" },
        { from: "deltas", to: "sort", label: "Δ list" },
        { from: "sort", to: "varstat", label: "sorted deltas" },
        { from: "varstat", to: "es", label: "VaR99 cutoff" },
        { from: "deltas", to: "mean", label: "Δ list" },
        { from: "deltas", to: "hist", label: "Δ list" },
        { from: "varstat", to: "api", label: "var_95 / var_99" },
        { from: "es", to: "api", label: "es_99" },
        { from: "mean", to: "api", label: "mean_daily" },
        { from: "hist", to: "api", label: "hist bins" },
        { from: "api", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "greeks-net", panel: "Net greeks", view: "risk", domain: "trade", isolated: true,
    cadence: "~2s · risk beat",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Net greeks")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open (Σ)", "render"],
    // /positions/open returns per-position rows; the Σ net-greeks reduction is front-side.
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "spot", kind: "container", label: "read spot+surface", sub: "risk-engine · GET latest_spot/latest_vol_surface (Redis)", role: "transform", health: "risk-engine" },
        { id: "load", kind: "container", label: "load OPEN book", sub: "risk-engine · SELECT open_position → parse_local_symbol → signed qty/K/T", role: "transform", health: "risk-engine" },
        { id: "iv", kind: "container", label: "resolve IV", sub: "risk-engine · _iv_for(surface,tenor,K) ∨ bs_implied_vol(mark)", role: "transform", health: "risk-engine" },
        { id: "bs", kind: "container", label: "price legs (BS)", sub: "risk-engine · bs_delta/gamma/vega/theta per leg", role: "transform", health: "risk-engine" },
        { id: "perpos", kind: "container", label: "per-position greeks", sub: "risk-engine · Δ=qty·δ·mult, Γ·1e-4, V·0.01, Θ", role: "transform", health: "risk-engine" },
        { id: "write", kind: "container", label: "UPDATE greeks", sub: "risk-engine · open_position.delta_usd/gamma_usd/vega_usd/theta_usd", role: "transform", health: "risk-engine" },
        { id: "pg", kind: "store", label: "Postgres", sub: "open_position · denormalised greeks", role: "receive", health: "postgres" },
        { id: "read", kind: "api", label: "read book", sub: "api · SELECT open_position rows", role: "transform", health: "__api" },
        { id: "api", kind: "api", label: "api", sub: "GET /positions/open", role: "hub", health: "__api" },
        { id: "sum", kind: "frontend", label: "Σ net greeks", sub: "frontend · sum Δ/Γ/V/Θ over rows", role: "transform", health: "__self" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Net greeks", sub: "Σ Δ/Γ/V/Θ", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "positions / market data" },
        { from: "ibg", to: "spot", label: "clientId 3" },
        { from: "spot", to: "load", label: "F, surface" },
        { from: "load", to: "iv", label: "legs (K,T,tenor)" },
        { from: "iv", to: "bs", label: "σ per leg" },
        { from: "bs", to: "perpos", label: "raw greeks" },
        { from: "perpos", to: "write", label: "scaled $ greeks" },
        { from: "write", to: "pg", label: "UPDATE (~30s throttle)" },
        { from: "pg", to: "read", label: "SELECT rows" },
        { from: "read", to: "api", label: "rows" },
        { from: "api", to: "sum", label: "GET /positions/open" },
        { from: "sum", to: "fe", label: "Σ Δ/Γ/V/Θ" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "vvv-tenor", panel: "Per-tenor greeks", view: "risk", domain: "risk", isolated: true,
    cadence: "~60s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Per-tenor greeks")],
    edges: ["UPDATE book", "read book", "GET /portfolio/risk-per-tenor", "render"],
    // 2nd-order greeks written per-leg by risk-engine; api buckets vega/vanna/volga by DTE.
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "load", kind: "container", label: "load OPEN book", sub: "risk-engine · SELECT open_position → legs", role: "transform", health: "risk-engine" },
        { id: "iv", kind: "container", label: "resolve IV", sub: "risk-engine · _iv_for(surface,tenor,K)", role: "transform", health: "risk-engine" },
        { id: "vega", kind: "container", label: "vega leg", sub: "risk-engine · qty·bs_vega·mult·0.01 ($/volpt)", role: "transform", health: "risk-engine" },
        { id: "vanna", kind: "container", label: "vanna leg", sub: "risk-engine · qty·bs_vanna·mult·0.01 (∂Δ/∂σ)", role: "transform", health: "risk-engine" },
        { id: "volga", kind: "container", label: "volga leg", sub: "risk-engine · qty·bs_volga·mult·0.01² (∂²P/∂σ²)", role: "transform", health: "risk-engine" },
        { id: "write", kind: "container", label: "UPDATE book", sub: "risk-engine · open_position.vega_usd/vanna_usd/volga_usd", role: "transform", health: "risk-engine" },
        { id: "pg", kind: "store", label: "Postgres", sub: "open_position · vega/vanna/volga + expiry", role: "receive", health: "postgres" },
        { id: "read", kind: "api", label: "read book", sub: "api · SELECT dte, vega/vanna/volga WHERE structure LIKE 'EUU%'", role: "transform", health: "__api" },
        { id: "bucket", kind: "api", label: "bucket by DTE", sub: "api · GREATEST(0,expiry−today) → 6 tenor buckets", role: "transform", health: "__api" },
        { id: "agg", kind: "api", label: "Σ per bucket", sub: "api · sum vega/vanna/volga, count n", role: "transform", health: "__api" },
        { id: "api", kind: "api", label: "api", sub: "GET /portfolio/risk-per-tenor", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Per-tenor greeks", sub: "vega/vanna/volga × tenor bucket", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "positions / market data" },
        { from: "ibg", to: "load", label: "clientId 3" },
        { from: "load", to: "iv", label: "legs (K,T,tenor)" },
        { from: "iv", to: "vega", label: "σ per leg" },
        { from: "iv", to: "vanna", label: "σ per leg" },
        { from: "iv", to: "volga", label: "σ per leg" },
        { from: "vega", to: "write", label: "vega_usd" },
        { from: "vanna", to: "write", label: "vanna_usd" },
        { from: "volga", to: "write", label: "volga_usd" },
        { from: "write", to: "pg", label: "UPDATE (~30s throttle)" },
        { from: "pg", to: "read", label: "SELECT dte+greeks" },
        { from: "read", to: "bucket", label: "rows" },
        { from: "bucket", to: "agg", label: "binned rows" },
        { from: "agg", to: "api", label: "6 buckets" },
        { from: "api", to: "fe", label: "GET /portfolio/risk-per-tenor" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "risk-util", panel: "Risk utilization", view: "risk", domain: "portfolio", isolated: true,
    cadence: "~60s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position · account_history"), API, FE, panel("Risk utilization")],
    edges: ["UPDATE book", "read greeks + account", "GET /portfolio/greek-limits + /account", "render"],
    // caps L*=α·nav projected per greek with a regime multiplier; utilization % is front-side.
    dag: {
      nodes: [
        { id: "engR", kind: "container", label: "risk-engine", sub: "greeks /2s → open_position", role: "transform", health: "risk-engine" },
        { id: "engX", kind: "container", label: "execution-engine", sub: "account snaps → account_history (nav, margin)", role: "transform", health: "exec-engine" },
        { id: "engV", kind: "container", label: "vol-engine", sub: "EURUSD spot + regime vol_level_pct", role: "transform", health: "vol-engine" },
        { id: "pgPos", kind: "store", label: "Postgres", sub: "open_position · live net greeks", role: "receive", health: "postgres" },
        { id: "pgAcct", kind: "store", label: "Postgres", sub: "account_history · 504d net_liq + margin", role: "receive", health: "postgres" },
        { id: "pgRgm", kind: "store", label: "Postgres", sub: "regime_snapshot · vol_level_pct (90d)", role: "receive", health: "postgres" },
        { id: "navbase", kind: "api", label: "nav_base anchor", sub: "api · max(0.9·HWM, EWMA-20d) daily net-liq", role: "transform", health: "__api" },
        { id: "regmult", kind: "api", label: "regime_mult", sub: "api · clamp(vol_level[-1]/median(90d), 1, 3)", role: "transform", health: "__api" },
        { id: "lstar", kind: "api", label: "loss budget L*", sub: "api · L* = α·nav_base (α=0.05)", role: "transform", health: "__api" },
        { id: "caps", kind: "api", label: "project caps", sub: "api · Δ=β·L*/s, V=β·L*/v, Γ=2β·L*/(s²·spot·1e4); s/v×regime_mult", role: "transform", health: "__api" },
        { id: "util", kind: "frontend", label: "utilization %", sub: "frontend · current greek ÷ cap per axis", role: "transform", health: "__self" },
        { id: "api", kind: "api", label: "api", sub: "GET /portfolio/greek-limits + /account", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Risk utilization", sub: "current greek ÷ cap", role: "receive", terminal: true },
      ],
      edges: [
        { from: "engR", to: "pgPos", label: "UPDATE greeks" },
        { from: "engX", to: "pgAcct", label: "INSERT account snap" },
        { from: "engV", to: "pgRgm", label: "INSERT regime snap" },
        { from: "pgAcct", to: "navbase", label: "504d net-liq series" },
        { from: "pgRgm", to: "regmult", label: "90d vol_level_pct" },
        { from: "navbase", to: "lstar", label: "nav_base" },
        { from: "lstar", to: "caps", label: "L*" },
        { from: "regmult", to: "caps", label: "regime_mult (s,v scale)" },
        { from: "engV", to: "caps", label: "spot" },
        { from: "caps", to: "api", label: "δ/v/γ/cross caps" },
        { from: "pgPos", to: "api", label: "current net greeks (/account margin)" },
        { from: "api", to: "util", label: "GET greek-limits + account" },
        { from: "util", to: "fe", label: "current ÷ cap" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "pin-risk", panel: "Pin risk", view: "risk", domain: "risk", isolated: true, cadence: "~120s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Pin risk")],
    edges: ["UPDATE book", "read options", "GET /portfolio/pin-risk (reval)", "render"],
    // 3 full-BS revals per near-expiry option (at strike ±50bp) vs NPV_now (api on-demand).
    dag: {
      nodes: [
        { id: "risk", kind: "container", label: "risk-engine", sub: "greeks /2s → open_position", role: "transform", health: "risk-engine" },
        { id: "vol", kind: "container", label: "vol-engine", sub: "calib → latest_vol_surface", role: "transform", health: "vol-engine" },
        { id: "pg", kind: "store", label: "Postgres", sub: "open_position · options", role: "receive", health: "postgres" },
        { id: "redis", kind: "store", label: "Redis", sub: "latest_vol_surface", role: "hub", health: "redis" },
        { id: "locate", kind: "api", label: "locate near-expiry options", sub: "OpenPosition · skip futures · sort by DTE", role: "transform", health: "__api" },
        { id: "surface", kind: "api", label: "read IV", sub: "position iv · spot proxy (FUTURE px / K)", role: "transform", health: "__api" },
        { id: "pin", kind: "api", label: "reval at pin", sub: "bs_price(spot=K) − NPV_now", role: "transform", health: "__api" },
        { id: "up", kind: "api", label: "reval breach +50bp", sub: "bs_price(K+50bp·K) − NPV_now", role: "transform", health: "__api" },
        { id: "dn", kind: "api", label: "reval breach −50bp", sub: "bs_price(K−50bp·K) − NPV_now", role: "transform", health: "__api" },
        { id: "expose", kind: "api", label: "assemble pin exposure", sub: "per-option ΔNPV rows · distance_pips · sort DTE", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Pin risk", sub: "ΔNPV at pin / breach ±50bp", role: "receive", terminal: true },
      ],
      edges: [
        { from: "risk", to: "pg", label: "UPDATE book" },
        { from: "vol", to: "redis", label: "publish" },
        { from: "pg", to: "locate", label: "read options" },
        { from: "redis", to: "surface", label: "read surface" },
        { from: "locate", to: "pin", label: "K, T, qty" },
        { from: "locate", to: "up", label: "K, T, qty" },
        { from: "locate", to: "dn", label: "K, T, qty" },
        { from: "surface", to: "pin", label: "IV" },
        { from: "surface", to: "up", label: "IV" },
        { from: "surface", to: "dn", label: "IV" },
        { from: "pin", to: "expose", label: "ΔNPV pin" },
        { from: "up", to: "expose", label: "ΔNPV up" },
        { from: "dn", to: "expose", label: "ΔNPV dn" },
        { from: "expose", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "marginal-var", panel: "Marginal VaR", view: "risk", domain: "risk", isolated: true,
    cadence: "~120s poll · component VaR (120d)",
    nodes: [eng("risk-engine", "per-pos pnl /2s"), pg("open_position_history"), API, FE, panel("Marginal VaR")],
    edges: ["INSERT snapshot", "daily pnl series", "GET /portfolio/marginal-var (Euler)", "render"],
    // Euler/component VaR: portfolio VaR allocated to each position by cov(i,pf)/var(pf).
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "risk", kind: "container", label: "risk-engine", sub: "clientId 3 · per-position greeks + P&L", role: "transform", health: "risk-engine" },
        { id: "snap", kind: "container", label: "snapshot", sub: "risk-engine · OpenPositionHistory row (~30s)", role: "transform", health: "risk-engine" },
        { id: "redis", kind: "store", label: "Redis", sub: "db_events", role: "hub", health: "redis" },
        { id: "dbw", kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive", health: "db-writer" },
        { id: "pghist", kind: "store", label: "Postgres", sub: "open_position_history (current_pnl_usd)", role: "receive", health: "postgres" },
        { id: "pgpos", kind: "store", label: "Postgres", sub: "open_position (delta/vega/vanna/volga_usd)", role: "receive", health: "postgres" },
        { id: "factor", kind: "api", label: "factor tag", sub: "api · argmax|greek| → spot/level/skew/curv", role: "transform", health: "__api" },
        { id: "daily", kind: "api", label: "daily P&L", sub: "api · DISTINCT ON (pos,day) pnl · 120d", role: "transform", health: "__api" },
        { id: "series", kind: "api", label: "per-pos Δ series", sub: "api · consecutive-day Δpnl (≥2 pts)", role: "transform", health: "__api" },
        { id: "align", kind: "api", label: "align matrix", sub: "core · right-align to common n (≥5d)", role: "transform", health: "__api" },
        { id: "pf", kind: "api", label: "portfolio P&L", sub: "core · row-sum → VaR_p (_loss_var 99%)", role: "transform", health: "__api" },
        { id: "stand", kind: "api", label: "standalone VaR", sub: "core · per-pos own loss quantile", role: "transform", health: "__api" },
        { id: "euler", kind: "api", label: "component VaR", sub: "core · VaR_p·cov(i,p)/var(p) Euler", role: "transform", health: "__api" },
        { id: "div", kind: "api", label: "diversification", sub: "core · 1 − VaR_p / Σstandalone", role: "transform", health: "__api" },
        { id: "api", kind: "api", label: "api", sub: "GET /portfolio/marginal-var", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Marginal VaR", sub: "per-position standalone · component · %VaR", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "position + mkt data" },
        { from: "ibg", to: "risk", label: "reqMktData / portfolio" },
        { from: "risk", to: "snap", label: "panel-E columns" },
        { from: "snap", to: "redis", label: "db_events" },
        { from: "redis", to: "dbw", label: "db_events" },
        { from: "dbw", to: "pghist", label: "INSERT (~30s)" },
        { from: "risk", to: "pgpos", label: "live greeks upsert" },
        { from: "pgpos", to: "factor", label: "read open positions" },
        { from: "pghist", to: "daily", label: "read pnl 120d" },
        { from: "daily", to: "series", label: "day-keyed pnl" },
        { from: "series", to: "align", label: "Δ series by id" },
        { from: "align", to: "pf", label: "(n_days × n_pos) matrix" },
        { from: "align", to: "stand", label: "per-pos column" },
        { from: "pf", to: "stand", label: "conf 99%" },
        { from: "pf", to: "euler", label: "VaR_p · var(pf)" },
        { from: "align", to: "euler", label: "cov(i, pf)" },
        { from: "stand", to: "div", label: "Σstandalone" },
        { from: "pf", to: "div", label: "VaR_p" },
        { from: "factor", to: "api", label: "factor + label + trade" },
        { from: "euler", to: "api", label: "component · %VaR" },
        { from: "stand", to: "api", label: "standalone" },
        { from: "div", to: "api", label: "portfolio_var · divers." },
        { from: "api", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "stress", panel: "Stress grids", view: "risk", domain: "risk", isolated: true, cadence: "~120s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Stress grids")],
    edges: ["UPDATE book", "read book", "GET /portfolio/stress-grid (reval ×4)", "render"],
    // spot-vol grid: reval_book at each (spot bin × vol row) shock vs price_base (api on-demand).
    dag: {
      nodes: [
        { id: "risk", kind: "container", label: "risk-engine", sub: "greeks /2s → open_position", role: "transform", health: "risk-engine" },
        { id: "vol", kind: "container", label: "vol-engine", sub: "calib → latest_vol_surface", role: "transform", health: "vol-engine" },
        { id: "pg", kind: "store", label: "Postgres", sub: "open_position · book", role: "receive", health: "postgres" },
        { id: "redis", kind: "store", label: "Redis", sub: "latest_vol_surface", role: "hub", health: "redis" },
        { id: "book", kind: "api", label: "resolve book", sub: "_resolve_book · spot proxy + baselines (price_base)", role: "transform", health: "__api" },
        { id: "surface", kind: "api", label: "read surface", sub: "latest_vol_surface · base IV", role: "transform", health: "__api" },
        { id: "spotup", kind: "api", label: "reval spot+", sub: "reval_book dspot_bp=+50/+100/+200", role: "transform", health: "__api" },
        { id: "spotdn", kind: "api", label: "reval spot−", sub: "reval_book dspot_bp=−50/−100/−200", role: "transform", health: "__api" },
        { id: "volup", kind: "api", label: "reval vol+", sub: "reval_book dvol_vp=+1/+3", role: "transform", health: "__api" },
        { id: "voldn", kind: "api", label: "reval vol−", sub: "reval_book dvol_vp=−1/−3", role: "transform", health: "__api" },
        { id: "assemble", kind: "api", label: "assemble grid", sub: "7 spot × 5 vol cells → ΔNPV grid", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Stress grids", sub: "spot × vol ΔNPV grid", role: "receive", terminal: true },
      ],
      edges: [
        { from: "risk", to: "pg", label: "UPDATE book" },
        { from: "vol", to: "redis", label: "publish" },
        { from: "pg", to: "book", label: "read book" },
        { from: "redis", to: "surface", label: "read surface" },
        { from: "book", to: "spotup", label: "baselines" },
        { from: "book", to: "spotdn", label: "baselines" },
        { from: "book", to: "volup", label: "baselines" },
        { from: "book", to: "voldn", label: "baselines" },
        { from: "surface", to: "volup", label: "shock IV" },
        { from: "surface", to: "voldn", label: "shock IV" },
        { from: "spotup", to: "assemble", label: "ΔNPV" },
        { from: "spotdn", to: "assemble", label: "ΔNPV" },
        { from: "volup", to: "assemble", label: "ΔNPV" },
        { from: "voldn", to: "assemble", label: "ΔNPV" },
        { from: "assemble", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "greeks-ladder", panel: "Greeks ladders", view: "risk", domain: "risk", isolated: true, cadence: "~120s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Greeks ladders")],
    edges: ["UPDATE book", "read book", "GET /portfolio/greeks-ladder (reval ×5)", "render"],
    // one full-BS reval_book per ladder axis (spot/vol/time/skew/fly) → pnl+Δ+Γ+vega per bin.
    dag: {
      nodes: [
        { id: "risk", kind: "container", label: "risk-engine", sub: "greeks /2s → open_position", role: "transform", health: "risk-engine" },
        { id: "vol", kind: "container", label: "vol-engine", sub: "calib → latest_vol_surface", role: "transform", health: "vol-engine" },
        { id: "pg", kind: "store", label: "Postgres", sub: "open_position · book", role: "receive", health: "postgres" },
        { id: "redis", kind: "store", label: "Redis", sub: "latest_vol_surface", role: "hub", health: "redis" },
        { id: "book", kind: "api", label: "resolve book", sub: "_resolve_book · spot proxy + baselines", role: "transform", health: "__api" },
        { id: "surface", kind: "api", label: "read surface", sub: "latest_vol_surface · base IV", role: "transform", health: "__api" },
        { id: "spot", kind: "api", label: "reval spot axis", sub: "full-BS reval_book ×[-400,-200,0,200,400]bp · pnl+Δ+Γ+vega", role: "transform", health: "__api" },
        { id: "vola", kind: "api", label: "reval vol axis", sub: "full-BS reval_book ×[-3,-1,0,1,3]vp", role: "transform", health: "__api" },
        { id: "time", kind: "api", label: "reval time axis", sub: "full-BS reval_book ×[0,5,10,20,40]d decay", role: "transform", health: "__api" },
        { id: "skew", kind: "api", label: "reval skew axis", sub: "full-BS reval_book ×[-2,-1,0,1,2]vp ΔRR", role: "transform", health: "__api" },
        { id: "fly", kind: "api", label: "reval fly axis", sub: "full-BS reval_book ×[-2,-1,0,1,2]vp ΔBF", role: "transform", health: "__api" },
        { id: "assemble", kind: "api", label: "assemble rows", sub: "per-bin pnl/Δ/Γ/vega + hedge_delta=−Δ", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Greeks ladders", sub: "5 axes · pnl/Δ/Γ/vega ladder", role: "receive", terminal: true },
      ],
      edges: [
        { from: "risk", to: "pg", label: "UPDATE book" },
        { from: "vol", to: "redis", label: "publish" },
        { from: "pg", to: "book", label: "read book" },
        { from: "redis", to: "surface", label: "read surface" },
        { from: "book", to: "spot", label: "baselines" },
        { from: "book", to: "vola", label: "baselines" },
        { from: "book", to: "time", label: "baselines" },
        { from: "book", to: "skew", label: "baselines" },
        { from: "book", to: "fly", label: "baselines" },
        { from: "surface", to: "vola", label: "shock IV" },
        { from: "surface", to: "skew", label: "shock IV" },
        { from: "surface", to: "fly", label: "shock IV" },
        { from: "spot", to: "assemble", label: "ladder" },
        { from: "vola", to: "assemble", label: "ladder" },
        { from: "time", to: "assemble", label: "ladder" },
        { from: "skew", to: "assemble", label: "ladder" },
        { from: "fly", to: "assemble", label: "ladder" },
        { from: "assemble", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "risk-macro", panel: "Macro events", view: "risk", domain: "trade", isolated: true, cadence: "~24h · events scheduler",
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Macro events")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
    // events scheduler (in api) fans out to providers every 24h → dedup → upsert event_calendar.
    dag: {
      nodes: [
        { id: "src", kind: "external", label: "macro providers", sub: "FRED · ECB · BoE · FOMC · Eurostat · ONS", role: "emit" },
        { id: "fetch", kind: "api", label: "fetch sources", sub: "events scheduler · parallel + per-source isolation", role: "transform", health: "__api" },
        { id: "dedup", kind: "api", label: "dedup by hash", sub: "events scheduler · drop already-seen events", role: "transform", health: "__api" },
        { id: "upsert", kind: "api", label: "upsert", sub: "events scheduler · INSERT ON CONFLICT DO NOTHING", role: "transform", health: "__api" },
        { id: "pg", kind: "store", label: "Postgres", sub: "event_calendar", role: "receive", health: "postgres" },
        { id: "read", kind: "api", label: "read upcoming", sub: "api · SELECT event WHERE scheduled_at > now ORDER BY scheduled_at LIMIT n", role: "transform", health: "__api" },
        { id: "api", kind: "api", label: "api", sub: "GET /regime/events", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Macro events", sub: "upcoming high-impact events", role: "receive", terminal: true },
      ],
      edges: [
        { from: "src", to: "fetch", label: "HTTP fetch (~24h)" },
        { from: "fetch", to: "dedup", label: "raw events" },
        { from: "dedup", to: "upsert", label: "new events" },
        { from: "upsert", to: "pg", label: "INSERT ON CONFLICT" },
        { from: "pg", to: "read", label: "SELECT future events" },
        { from: "read", to: "api", label: "rows" },
        { from: "api", to: "fe", label: "GET /regime/events" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "position-breakdown", panel: "Positions", view: "risk", domain: "portfolio", isolated: true,
    cadence: "~2s · risk beat",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Positions")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open", "render"],
    // same producer as net greeks, but one row per live contract (no aggregation), grouped front-side.
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "spot", kind: "container", label: "read spot+surface", sub: "risk-engine · Redis latest_spot/latest_vol_surface", role: "transform", health: "risk-engine" },
        { id: "load", kind: "container", label: "load OPEN book", sub: "risk-engine · SELECT open_position → parse_local_symbol", role: "transform", health: "risk-engine" },
        { id: "iv", kind: "container", label: "resolve IV", sub: "risk-engine · _iv_for(surface,tenor,K) ∨ bs_implied_vol(mark)", role: "transform", health: "risk-engine" },
        { id: "bs", kind: "container", label: "price legs (BS)", sub: "risk-engine · bs_delta/gamma/vega/theta/vanna/volga + bs_price", role: "transform", health: "risk-engine" },
        { id: "perpos", kind: "container", label: "per-position greeks", sub: "risk-engine · scaled $ greeks + IB unrealized_pnl + mark", role: "transform", health: "risk-engine" },
        { id: "write", kind: "container", label: "UPDATE row", sub: "risk-engine · open_position.{delta..volga,iv,pnl,mark}", role: "transform", health: "risk-engine" },
        { id: "pg", kind: "store", label: "Postgres", sub: "open_position · 1 row / live contract", role: "receive", health: "postgres" },
        { id: "read", kind: "api", label: "read book", sub: "api · SELECT open_position ORDER BY entry_timestamp", role: "transform", health: "__api" },
        { id: "api", kind: "api", label: "api", sub: "GET /positions/open", role: "hub", health: "__api" },
        { id: "group", kind: "frontend", label: "group rows", sub: "frontend · per-position rows by package/trade id", role: "transform", health: "__self" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "Positions", sub: "per-position Δ/Γ/V/Θ/vanna/volga · PnL", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "positions / market data" },
        { from: "ibg", to: "spot", label: "clientId 3" },
        { from: "spot", to: "load", label: "F, surface" },
        { from: "load", to: "iv", label: "legs (K,T,tenor)" },
        { from: "iv", to: "bs", label: "σ per leg" },
        { from: "bs", to: "perpos", label: "raw greeks + price" },
        { from: "perpos", to: "write", label: "row fields" },
        { from: "write", to: "pg", label: "UPDATE (~30s throttle)" },
        { from: "pg", to: "read", label: "SELECT rows" },
        { from: "read", to: "api", label: "rows" },
        { from: "api", to: "group", label: "GET /positions/open" },
        { from: "group", to: "fe", label: "per-position rows" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },

  // ───────────────────────── Portfolio ─────────────────────────
  {
    id: "account", panel: "Account & capital", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), DBW, pg("account_history"), API, FE, panel("Account & capital")],
    edges: ["account summary", "reqAccountSummary", "db_events", "INSERT", "latest + 24h", "GET /portfolio/account", "render"],
    dag: dagPersist(xEng("execution-engine", "account snaps", "exec-engine"), "account summary", "publish", "account_history", "GET /portfolio/account", "Account & capital"),
  },
  {
    id: "perf", panel: "Performance", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("execution-engine", "account + close"), pg("account_history · booked_position"), API, FE, panel("Performance")],
    edges: ["INSERT", "daily series", "GET /portfolio/stats", "render"],
    dag: dagPersist(xEng("execution-engine", "account + close", "exec-engine"), "account summary", "publish", "account_history · booked_position", "GET /portfolio/stats", "Performance", "transform"),
  },
  {
    id: "carry-convex", panel: "Carry vs convexity", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Carry vs convexity")],
    edges: ["UPDATE Γ/Θ", "read book", "GET /portfolio/aggregate-greeks", "render"],
    dag: dagPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE Γ/Θ", "open_position", "GET /portfolio/aggregate-greeks", "Carry vs convexity"),
  },
  {
    id: "pnl-attribution", panel: "Realized P&L attribution", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("execution-engine", "MTM /2s"), pg("booked_position_metric_history"), API, FE, panel("P&L attribution")],
    edges: ["INSERT MTM", "Taylor decomp", "GET /portfolio/pnl-attribution", "render"],
    dag: dagPersist(xEng("execution-engine", "MTM /2s", "exec-engine"), "fills", "INSERT MTM", "booked_position_metric_history", "GET /portfolio/pnl-attribution · Taylor", "P&L attribution", "transform"),
  },
  {
    id: "book-composition", panel: "Book composition", view: "portfolio", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Book composition")],
    edges: ["UPDATE book", "read book", "GET /positions/open (grouped)", "render"],
    dag: dagPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE book", "open_position", "GET /positions/open (grouped)", "Book composition"),
  },
];
