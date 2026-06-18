/**
 * Declarative data-pipeline model per front panel (dev "Pipeline" tab).
 *
 * Drawn as a plumbing/network schematic: container blocks joined by pipes with
 * an animated "current" that only flows when the panel's data domain is live.
 * The pipeline terminates at the panel itself (rendered live on the right) —
 * the final pipe feeds the panel's west face, labelled with `endpoint`.
 *
 * `nodes` run source → api (the panel is the frontend destination, not a node).
 * `edges.length === nodes.length - 1`. `domain` is the DeskData key whose
 * freshness drives the flow animation + the "last update" stamp.
 */
export type ViewId = "signals" | "trade" | "portfolio" | "risk" | "dashboard" | "system";

export type DomainId =
  | "surface" | "pca" | "trade" | "portfolio" | "risk" | "system" | "termStructure" | "ticks";

export type NodeKind = "external" | "container" | "store" | "api";

export interface PipeNode {
  kind: NodeKind;
  label: string;
  sub?: string;
}

export interface PanelPipe {
  id: string;
  panel: string; // listbox label
  view: ViewId; // which voldesk view is rendered live on the right
  domain: DomainId; // freshness source (flow animation + last-update stamp)
  endpoint: string; // label on the final pipe into the panel's west face
  nodes: PipeNode[]; // source → api
  edges: string[]; // labels between consecutive nodes (length = nodes-1)
}

const ibg: PipeNode = { kind: "container", label: "ib-gateway", sub: "broker session" };

export const PIPELINES: PanelPipe[] = [
  {
    id: "ticks", panel: "Spot ticks (Dashboard)", view: "dashboard", domain: "ticks",
    endpoint: "WS /ws/ticks",
    nodes: [
      { kind: "external", label: "IB", sub: "live market data" },
      ibg,
      { kind: "container", label: "market-data", sub: "clientId 1" },
      { kind: "store", label: "Redis", sub: "latest_spot:EUR · ticks ch." },
      { kind: "api", label: "api", sub: "WS bridge" },
    ],
    edges: ["get tick", "reqMktData", "publish ticks", "subscribe"],
  },
  {
    id: "iv-surface", panel: "IV surface heatmap (Signals)", view: "signals", domain: "surface",
    endpoint: "GET /vol/surface",
    nodes: [
      { kind: "external", label: "IB", sub: "FOP option chain" },
      ibg,
      { kind: "container", label: "vol-engine", sub: "clientId 2 · 180s cycle" },
      { kind: "store", label: "Redis + Postgres", sub: "latest_vol_surface · vol_surface_history" },
      { kind: "api", label: "api", sub: "vol router" },
    ],
    edges: ["chain + greeks", "reqMktData", "compute surface (SET + db_events)", "read latest"],
  },
  {
    id: "pca-signals", panel: "PCA mode cards (Signals)", view: "signals", domain: "pca",
    endpoint: "GET /signals/pca/state",
    nodes: [
      { kind: "container", label: "vol-engine", sub: "projects surface on PCA" },
      { kind: "store", label: "Postgres", sub: "pca_model · pca_signal_history" },
      { kind: "api", label: "api", sub: "signals router" },
    ],
    edges: ["project + persist (db_events)", "read active model + signals"],
  },
  {
    id: "positions", panel: "Position breakdown (Trade / Risk)", view: "trade", domain: "trade",
    endpoint: "GET /positions/open",
    nodes: [
      { kind: "external", label: "IB", sub: "open positions" },
      ibg,
      { kind: "container", label: "execution-engine", sub: "clientId 5 · position sync" },
      { kind: "container", label: "risk-engine", sub: "clientId 3 · greeks /2s" },
      { kind: "store", label: "Postgres", sub: "open_position" },
      { kind: "api", label: "api", sub: "positions router" },
    ],
    edges: ["positions", "reqPositions", "UPDATE greeks", "UPSERT row", "read book"],
  },
  {
    id: "var", panel: "Value at Risk (Risk)", view: "risk", domain: "risk",
    endpoint: "GET /portfolio/var",
    nodes: [
      { kind: "external", label: "IB", sub: "account updates" },
      ibg,
      { kind: "container", label: "execution-engine", sub: "account snapshots" },
      { kind: "store", label: "Postgres", sub: "account_history (504d)" },
      { kind: "api", label: "api", sub: "historical-sim VaR" },
    ],
    edges: ["account summary", "reqAccountSummary", "INSERT account_history", "504d net-liq deltas"],
  },
  {
    id: "stress", panel: "Stress engine (Risk)", view: "risk", domain: "risk",
    endpoint: "GET /portfolio/stress-grid",
    nodes: [
      { kind: "container", label: "risk-engine", sub: "writes greeks /2s" },
      { kind: "store", label: "Postgres", sub: "open_position" },
      { kind: "api", label: "api", sub: "full-BS reval per cell" },
    ],
    edges: ["UPDATE book", "read open book"],
  },
  {
    id: "vega-pca", panel: "vega → PCA mode (Risk)", view: "risk", domain: "risk",
    endpoint: "GET /portfolio/vega-pca",
    nodes: [
      { kind: "container", label: "risk-engine + vol-engine", sub: "greeks + PCA model" },
      { kind: "store", label: "Postgres", sub: "open_position · pca_model" },
      { kind: "api", label: "api", sub: "project vega × loadings" },
    ],
    edges: ["greeks + active model", "read book + loadings"],
  },
  {
    id: "marginal-var", panel: "Marginal VaR (Risk)", view: "risk", domain: "risk",
    endpoint: "GET /portfolio/marginal-var",
    nodes: [
      { kind: "container", label: "risk-engine", sub: "per-position pnl /2s" },
      { kind: "store", label: "Postgres", sub: "open_position_history" },
      { kind: "api", label: "api", sub: "component VaR (Euler)" },
    ],
    edges: ["INSERT snapshot", "daily pnl series"],
  },
  {
    id: "equity-curve", panel: "Equity curve (Portfolio)", view: "portfolio", domain: "portfolio",
    endpoint: "GET /portfolio/equity-curve",
    nodes: [
      { kind: "container", label: "execution-engine", sub: "account snapshots" },
      { kind: "store", label: "Postgres", sub: "account_history" },
      { kind: "api", label: "api", sub: "adaptive downsample" },
    ],
    edges: ["INSERT net-liq", "windowed query"],
  },
  {
    id: "engines-health", panel: "Engine health (System)", view: "system", domain: "system",
    endpoint: "GET /health/extended",
    nodes: [
      { kind: "container", label: "5 engines", sub: "heartbeat each cycle" },
      { kind: "store", label: "Redis", sub: "heartbeat:<engine>" },
      { kind: "api", label: "api", sub: "health router" },
    ],
    edges: ["SET heartbeat (TTL)", "read heartbeats"],
  },
];
