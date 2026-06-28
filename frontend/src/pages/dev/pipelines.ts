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
// engine → persisted → api serves the LIVE value from the Redis cache. Redis
// fans out (db-writer + api), Postgres written-then-read (dual role), api 2 inputs.
function dagFork(engine: PipeNode, redisSub: string, e0: string, e1: string, pgSub: string, apiSub: string, panelName: string, apiRole: Role = "hub"): PipeDag {
  return {
    nodes: [toDag(xIB, "ib"), toDag(xIBG, "ibg"), toDag(engine, "eng"), toDag(xRedis(redisSub), "redis"), toDag(xDBW, "dbw"), toDag(xPg(pgSub), "pg"), dApi(apiSub, apiRole), toDag(xFE, "fe"), dPanel(panelName)],
    edges: [
      { from: "ib", to: "ibg", label: e0 }, { from: "ibg", to: "eng", label: e1 },
      { from: "eng", to: "redis", label: "SET + db_events" },
      { from: "redis", to: "dbw", label: "db_events" }, { from: "dbw", to: "pg", label: "INSERT" },
      { from: "redis", to: "api", label: "read latest (live)" }, { from: "pg", to: "api", label: "history" },
      { from: "api", to: "fe", label: "JSON" }, { from: "fe", to: "panel", label: "render" },
    ],
  };
}
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
// computed on demand by the api from several converging sources (no store).
function dagReval(sources: PipeNode[], sourceEdges: string[], apiSub: string, panelName: string): PipeDag {
  return {
    nodes: [...sources.map((s, i) => toDag(s, `s${i}`)), dApi(apiSub, "transform"), toDag(xFE, "fe"), dPanel(panelName)],
    edges: [
      ...sources.map((_, i): DagEdge => ({ from: `s${i}`, to: "api", label: sourceEdges[i] ?? "" })),
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
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "vol", kind: "container", label: "vol-engine", sub: "clientId 2 · 180s cycle", role: "transform", health: "vol-engine" },
        { id: "redis", kind: "store", label: "Redis", sub: "SET latest_vol_surface + db_events", role: "hub", health: "redis" },
        { id: "dbw", kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive", health: "db-writer" },
        { id: "pg", kind: "store", label: "Postgres", sub: "vol_surface_history", role: "receive", health: "postgres" },
        { id: "api", kind: "api", label: "api", sub: "FastAPI · GET /vol/surface", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "IV surface", sub: "displayed panel", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "FOP chain" },
        { from: "ibg", to: "vol", label: "reqMktData" },
        { from: "vol", to: "redis", label: "SVI/SSVI + db_events" },
        { from: "redis", to: "dbw", label: "db_events" },
        { from: "dbw", to: "pg", label: "INSERT" },
        { from: "redis", to: "api", label: "read latest (live)" },
        { from: "pg", to: "api", label: "history · z-score" },
        { from: "api", to: "fe", label: "GET /vol/surface" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },
  {
    id: "fair-vol", panel: "Fair vol", view: "signals", domain: "termStructure", isolated: true,
    nodes: [eng("vol-engine", "YZ-RV · HAR/GARCH · VRP"), redis("latest_vol_surface"), API, FE, panel("Fair vol")],
    edges: ["σ_fair^Q (SET)", "read", "GET /vol/term-structure", "render"],
    dag: dagFork(xEng("vol-engine", "YZ-RV · HAR/GARCH · VRP", "vol-engine"), "latest_vol_surface (σ_fair)", "FOP chain", "reqMktData", "vol_surface_history", "GET /vol/term-structure", "Fair vol"),
  },
  {
    id: "pca-modes", panel: "PCA engine — surface modes", view: "signals", domain: "pca", isolated: true,
    cadence: "~3 min read · refit hourly (≥6 snaps)",
    nodes: [eng("vol-engine", "fit ≥6 snaps + project"), DBW, pg("snapshot_history → pca_model"), API, FE, panel("PCA modes")],
    edges: ["snap + fit (db_events)", "INSERT", "read model + count", "GET /signals/pca/*", "render"],
    dag: {
      nodes: [
        { id: "ib", kind: "external", label: "IB", sub: "Interactive Brokers", role: "emit", health: "IB Gateway" },
        { id: "ibg", kind: "container", label: "ib-gateway", sub: "broker session · clientId 1–5", role: "hub", health: "IB Gateway" },
        { id: "vol", kind: "container", label: "vol-engine", sub: "clientId 2 · hourly snap + PCA fit (≥6)", role: "transform", health: "vol-engine" },
        { id: "redis", kind: "store", label: "Redis", sub: "db_events", role: "hub", health: "redis" },
        { id: "dbw", kind: "container", label: "db-writer", sub: "db_events → batch INSERT", role: "receive", health: "db-writer" },
        { id: "pgsnap", kind: "store", label: "Postgres", sub: "pca_surface_snapshot_history (hourly)", role: "receive", health: "postgres" },
        { id: "pgmodel", kind: "store", label: "Postgres", sub: "pca_model · pca_signal_history", role: "receive", health: "postgres" },
        { id: "api", kind: "api", label: "api", sub: "GET /signals/pca/model · /state · /history", role: "hub", health: "__api" },
        { id: "fe", kind: "frontend", label: "frontend", sub: "React · fetch", role: "receive", health: "__self" },
        { id: "panel", kind: "panel", label: "PCA modes", sub: "displayed panel", role: "receive", terminal: true },
      ],
      edges: [
        { from: "ib", to: "ibg", label: "FOP chain" },
        { from: "ibg", to: "vol", label: "reqMktData" },
        { from: "vol", to: "redis", label: "hourly snap + fit (db_events)" },
        { from: "redis", to: "dbw", label: "db_events" },
        { from: "dbw", to: "pgsnap", label: "INSERT hourly" },
        { from: "dbw", to: "pgmodel", label: "INSERT on refit (≥6)" },
        { from: "pgsnap", to: "api", label: "snapshot count" },
        { from: "pgmodel", to: "api", label: "read pcs + state" },
        { from: "api", to: "fe", label: "JSON" },
        { from: "fe", to: "panel", label: "render" },
      ],
    },
  },

  // ───────────────────────── Risk ─────────────────────────
  // Rebuild in progress — one pipeline per DATA PATH (endpoint), not per visual
  // panel (Stress grids / Greeks ladders collapse to one each; the Greeks 2×2
  // splits by source). No schemas yet → the canvas renders blank for these.
  {
    id: "var", panel: "VaR", view: "risk", domain: "risk", isolated: true,
    nodes: [IB, IBG, eng("execution-engine", "account snaps"), DBW, pg("account_history (504d)"), API, FE, panel("VaR")],
    edges: ["account summary", "reqAccountSummary", "db_events", "INSERT", "504d sim", "GET /portfolio/var", "render"],
    dag: dagPersist(xEng("execution-engine", "account snaps", "exec-engine"), "account summary", "publish", "account_history (504d)", "GET /portfolio/var · sim", "VaR", "transform"),
  },
  {
    id: "greeks-net", panel: "Net greeks", view: "risk", domain: "trade", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Net greeks")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open (Σ)", "render"],
    dag: dagPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE greeks", "open_position", "GET /positions/open · Σ net greeks", "Net greeks"),
  },
  {
    id: "vvv-tenor", panel: "Per-tenor greeks", view: "risk", domain: "risk", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Per-tenor greeks")],
    edges: ["UPDATE book", "read book", "GET /portfolio/risk-per-tenor", "render"],
    dag: dagPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE book", "open_position", "GET /portfolio/risk-per-tenor · vega/vanna/volga", "Per-tenor greeks"),
  },
  {
    id: "risk-util", panel: "Risk utilization", view: "risk", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position · account_history"), API, FE, panel("Risk utilization")],
    edges: ["UPDATE book", "read greeks + account", "GET /portfolio/greek-limits + /account", "render"],
    dag: dagReval([xPg("open_position · net greeks"), xPg("account_history · nav_base + margin")], ["read greeks", "read account"], "GET /portfolio/greek-limits (L*=α·nav) + /account margin", "Risk utilization"),
  },
  {
    id: "pin-risk", panel: "Pin risk", view: "risk", domain: "risk", isolated: true, cadence: "~120s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Pin risk")],
    edges: ["UPDATE book", "read options", "GET /portfolio/pin-risk (reval)", "render"],
    dag: dagReval([xPg("open_position · options"), xRedis("latest_vol_surface")], ["read options", "read surface"], "GET /portfolio/pin-risk · reval at strike", "Pin risk"),
  },
  {
    id: "marginal-var", panel: "Marginal VaR", view: "risk", domain: "risk", isolated: true, cadence: "~120s · poll",
    nodes: [eng("risk-engine", "per-pos pnl /2s"), pg("open_position_history"), API, FE, panel("Marginal VaR")],
    edges: ["INSERT snapshot", "daily pnl series", "GET /portfolio/marginal-var (Euler)", "render"],
    dag: dagPersist(xEng("risk-engine", "per-pos pnl /2s", "risk-engine"), "positions", "INSERT snapshot", "open_position_history", "GET /portfolio/marginal-var · Euler allocation", "Marginal VaR", "transform"),
  },
  {
    id: "stress", panel: "Stress grids", view: "risk", domain: "risk", isolated: true, cadence: "~120s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Stress grids")],
    edges: ["UPDATE book", "read book", "GET /portfolio/stress-grid (reval ×4)", "render"],
    dag: dagReval([xPg("open_position · book"), xRedis("latest_vol_surface")], ["read book", "read surface"], "GET /portfolio/stress-grid · reval_book ×4 axes", "Stress grids"),
  },
  {
    id: "greeks-ladder", panel: "Greeks ladders", view: "risk", domain: "risk", isolated: true, cadence: "~120s · poll",
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Greeks ladders")],
    edges: ["UPDATE book", "read book", "GET /portfolio/greeks-ladder (reval ×5)", "render"],
    dag: dagReval([xPg("open_position · book"), xRedis("latest_vol_surface")], ["read book", "read surface"], "GET /portfolio/greeks-ladder · full-BS reval ×5 axes", "Greeks ladders"),
  },
  {
    id: "risk-macro", panel: "Macro events", view: "risk", domain: "trade", isolated: true, cadence: "~24h · events scheduler",
    nodes: [eng("api · events scheduler", "FRED/ECB/BoE/FOMC"), pg("event_calendar"), API, FE, panel("Macro events")],
    edges: ["fetch + dedup", "upsert", "GET /regime/events", "render"],
    dag: dagEvents("Macro events"),
  },
  {
    id: "position-breakdown", panel: "Positions", view: "risk", domain: "portfolio", isolated: true,
    nodes: [eng("risk-engine", "greeks /2s"), pg("open_position"), API, FE, panel("Positions")],
    edges: ["UPDATE greeks", "read book", "GET /positions/open", "render"],
    dag: dagPersist(xEng("risk-engine", "greeks /2s", "risk-engine"), "positions", "UPDATE greeks", "open_position", "GET /positions/open · per-position greeks", "Positions"),
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
