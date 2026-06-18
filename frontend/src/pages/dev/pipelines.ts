/**
 * Declarative data-pipeline model per front panel (dev "Pipeline" tab).
 *
 * Each panel maps to an ordered chain of nodes (source → … → frontend) drawn as
 * blocks + labelled arrows. Nodes are the real topology blocks: the external
 * broker/feed, our Python containers, the stores (Postgres / Redis), the api,
 * and the frontend that renders the panel. Edge labels describe *what* moves
 * between two blocks (no function names — the point is to see the pipeline).
 *
 * `view` selects which voldesk view is rendered live on the right (the panel
 * lives inside it). Hand-authored from the real wiring — extend freely.
 */
export type ViewId = "signals" | "trade" | "portfolio" | "risk" | "dashboard" | "system";

export type NodeKind = "external" | "container" | "store" | "api" | "frontend";

export interface PipeNode {
  kind: NodeKind;
  label: string;
  sub?: string;
}

export interface PanelPipe {
  id: string;
  panel: string; // human label shown in the listbox
  view: ViewId;
  endpoint?: string; // headline endpoint/channel feeding the panel
  nodes: PipeNode[]; // source → frontend
  edges: string[]; // length = nodes.length - 1
}

const ibg: PipeNode = { kind: "container", label: "ib-gateway", sub: "broker session" };

export const PIPELINES: PanelPipe[] = [
  {
    id: "ticks",
    panel: "Spot ticks (Dashboard)",
    view: "dashboard",
    endpoint: "WS /ws/ticks",
    nodes: [
      { kind: "external", label: "IB", sub: "live market data" },
      ibg,
      { kind: "container", label: "market-data", sub: "clientId 1" },
      { kind: "store", label: "Redis", sub: "latest_spot:EUR · ticks channel" },
      { kind: "api", label: "api", sub: "WS bridge" },
      { kind: "frontend", label: "frontend", sub: "Dashboard · spot tick" },
    ],
    edges: ["get tick", "reqMktData", "publish ticks", "subscribe", "WS /ws/ticks"],
  },
  {
    id: "iv-surface",
    panel: "IV surface heatmap (Signals)",
    view: "signals",
    endpoint: "GET /vol/surface",
    nodes: [
      { kind: "external", label: "IB", sub: "FOP option chain" },
      ibg,
      { kind: "container", label: "vol-engine", sub: "clientId 2 · 180s cycle" },
      { kind: "store", label: "Redis + Postgres", sub: "latest_vol_surface · vol_surface_history" },
      { kind: "api", label: "api", sub: "vol router" },
      { kind: "frontend", label: "frontend", sub: "Signals · IV surface" },
    ],
    edges: ["chain + greeks", "reqMktData", "compute surface (SET + db_events)", "read latest", "GET /vol/surface"],
  },
  {
    id: "pca-signals",
    panel: "PCA mode cards (Signals)",
    view: "signals",
    endpoint: "GET /signals/pca/state",
    nodes: [
      { kind: "container", label: "vol-engine", sub: "projects surface on PCA" },
      { kind: "store", label: "Postgres", sub: "pca_model · pca_signal_history" },
      { kind: "api", label: "api", sub: "signals router" },
      { kind: "frontend", label: "frontend", sub: "Signals · PC1/2/3 cards" },
    ],
    edges: ["project + persist (db_events)", "read active model + signals", "GET /signals/pca/state"],
  },
  {
    id: "positions",
    panel: "Position breakdown (Trade / Risk)",
    view: "trade",
    endpoint: "GET /positions/open",
    nodes: [
      { kind: "external", label: "IB", sub: "open positions" },
      ibg,
      { kind: "container", label: "execution-engine", sub: "clientId 5 · position sync" },
      { kind: "container", label: "risk-engine", sub: "clientId 3 · greeks /2s" },
      { kind: "store", label: "Postgres", sub: "open_position" },
      { kind: "api", label: "api", sub: "positions router" },
      { kind: "frontend", label: "frontend", sub: "Trade · positions" },
    ],
    edges: ["positions", "reqPositions", "UPDATE greeks", "UPSERT row", "read book", "GET /positions/open"],
  },
  {
    id: "var",
    panel: "Value at Risk (Risk)",
    view: "risk",
    endpoint: "GET /portfolio/var",
    nodes: [
      { kind: "external", label: "IB", sub: "account updates" },
      ibg,
      { kind: "container", label: "execution-engine", sub: "account snapshots" },
      { kind: "store", label: "Postgres", sub: "account_history (504d)" },
      { kind: "api", label: "api", sub: "historical-sim VaR" },
      { kind: "frontend", label: "frontend", sub: "Risk · VaR card" },
    ],
    edges: ["account summary", "reqAccountSummary", "INSERT account_history", "504d net-liq deltas", "GET /portfolio/var"],
  },
  {
    id: "stress",
    panel: "Stress engine (Risk)",
    view: "risk",
    endpoint: "GET /portfolio/stress-grid",
    nodes: [
      { kind: "container", label: "risk-engine", sub: "writes greeks /2s" },
      { kind: "store", label: "Postgres", sub: "open_position" },
      { kind: "api", label: "api", sub: "full-BS reval per cell" },
      { kind: "frontend", label: "frontend", sub: "Risk · stress grids" },
    ],
    edges: ["UPDATE book", "read open book", "GET /portfolio/stress-grid?axis=&output="],
  },
  {
    id: "vega-pca",
    panel: "vega → PCA mode (Risk)",
    view: "risk",
    endpoint: "GET /portfolio/vega-pca",
    nodes: [
      { kind: "container", label: "risk-engine + vol-engine", sub: "greeks + PCA model" },
      { kind: "store", label: "Postgres", sub: "open_position · pca_model" },
      { kind: "api", label: "api", sub: "project vega × loadings" },
      { kind: "frontend", label: "frontend", sub: "Risk · vega-PCA card" },
    ],
    edges: ["greeks + active model", "read book + loadings", "GET /portfolio/vega-pca"],
  },
  {
    id: "equity-curve",
    panel: "Equity curve (Portfolio)",
    view: "portfolio",
    endpoint: "GET /portfolio/equity-curve",
    nodes: [
      { kind: "container", label: "execution-engine", sub: "account snapshots" },
      { kind: "store", label: "Postgres", sub: "account_history" },
      { kind: "api", label: "api", sub: "adaptive downsample" },
      { kind: "frontend", label: "frontend", sub: "Portfolio · equity curve" },
    ],
    edges: ["INSERT net-liq", "windowed query", "GET /portfolio/equity-curve"],
  },
  {
    id: "engines-health",
    panel: "Engine health (System)",
    view: "system",
    endpoint: "GET /health/extended",
    nodes: [
      { kind: "container", label: "5 engines", sub: "heartbeat each cycle" },
      { kind: "store", label: "Redis", sub: "heartbeat:<engine>" },
      { kind: "api", label: "api", sub: "health router" },
      { kind: "frontend", label: "frontend", sub: "System · engine status" },
    ],
    edges: ["SET heartbeat (TTL)", "read heartbeats", "GET /health/extended"],
  },
];
