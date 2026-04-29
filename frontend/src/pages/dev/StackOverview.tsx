/**
 * Stack Overview — schéma SVG type draw.io des 10 containers + flèches de
 * dépendance, status live coloré (cf. docs/schémas/containers-overview.drawio
 * pour la version source). Auto-refresh 5s.
 *
 * Layout 4 rangées :
 *   Row 1 (data)    : redis · postgres · ib-gateway
 *   Row 2 (engines) : market-data · vol-engine · risk-engine · db-writer
 *   Row 3 (server)  : api
 *   Row 4 (edge)    : nginx · frontend
 *
 * Arrow convention : A → B veut dire "B utilise A" (cf. container_deps.md).
 * Les engines sont au milieu parce qu'ils sont la couche métier centrale.
 */
import { useEffect, useRef, useState } from "react";

interface Container {
  name: string;
  image: string;
  status: "OK" | "DOWN" | "STALE";
  desc: string;
}

interface StackResp {
  containers: Container[];
  timestamp: string;
}

const POLL_MS = 5_000;

// Coordonnées (x, y) en unités SVG. ViewBox "0 0 W H".
const BOX_W = 150;
const BOX_H = 60;
const SVG_W = 900;
const SVG_H = 540;

// Position de chaque box (centre top-left). 4 colonnes × 4 rangées.
const POSITIONS: Record<string, { x: number; y: number }> = {
  // Row 1 (data sources)
  "redis":      { x: 100, y: 30 },
  "postgres":   { x: 375, y: 30 },
  "ib-gateway": { x: 650, y: 30 },
  // Row 2 (engines)
  "market-data": { x: 30,  y: 180 },
  "vol-engine":  { x: 220, y: 180 },
  "risk-engine": { x: 410, y: 180 },
  "db-writer":   { x: 600, y: 180 },
  // Row 3 (api)
  "api":         { x: 375, y: 330 },
  // Row 4 (edge)
  "nginx":       { x: 280, y: 450 },
  "frontend":    { x: 470, y: 450 },
};

// Edges = (from, to). "from" est utilisé par "to" (ex: redis → api = api utilise redis).
const EDGES: { from: string; to: string }[] = [
  // data → engines
  { from: "redis",      to: "market-data" },
  { from: "redis",      to: "vol-engine" },
  { from: "redis",      to: "risk-engine" },
  { from: "redis",      to: "db-writer" },
  { from: "postgres",   to: "db-writer" },
  { from: "ib-gateway", to: "market-data" },
  { from: "ib-gateway", to: "vol-engine" },
  { from: "ib-gateway", to: "risk-engine" },
  // data → api
  { from: "redis",    to: "api" },
  { from: "postgres", to: "api" },
  // edges → api
  { from: "api",      to: "nginx" },
  { from: "frontend", to: "nginx" },
];

export function StackOverview(): JSX.Element {
  const [data, setData] = useState<StackResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const timerRef = useRef<number | null>(null);

  const fetchData = async () => {
    setError(null);
    try {
      const r = await fetch("/api/v1/dev/stack");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    fetchData();
    if (!autoRefresh) return;
    timerRef.current = window.setInterval(fetchData, POLL_MS);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [autoRefresh]);

  const containerByName: Record<string, Container> = {};
  if (data) for (const c of data.containers) containerByName[c.name] = c;

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <button onClick={fetchData} style={btnStyle}>Refresh</button>
        <label style={{ color: "#aaa", fontSize: 13 }}>
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            style={{ marginRight: 6 }}
          />
          Auto-refresh ({POLL_MS / 1000}s)
        </label>
        {data && (
          <span style={{ color: "#666", fontSize: 12, marginLeft: "auto" }}>
            last: {new Date(data.timestamp).toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <div style={{ color: "#e66", marginBottom: 12 }}>{error}</div>}

      <div style={{ background: "#0a0a0a", border: "1px solid #222", borderRadius: 4, padding: 12 }}>
        <svg
          viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          style={{ width: "100%", maxWidth: 1100, height: "auto", display: "block", margin: "0 auto" }}
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* Marker for arrowheads */}
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#666" />
            </marker>
          </defs>

          {/* Layer labels */}
          <text x="20" y="18" style={layerLabelSvg}>DATA</text>
          <text x="20" y="168" style={layerLabelSvg}>ENGINES</text>
          <text x="20" y="318" style={layerLabelSvg}>API</text>
          <text x="20" y="438" style={layerLabelSvg}>EDGE</text>

          {/* Edges (drawn first so boxes overlay them) */}
          {EDGES.map((e, i) => (
            <Arrow key={i} from={POSITIONS[e.from]} to={POSITIONS[e.to]} />
          ))}

          {/* Boxes */}
          {Object.entries(POSITIONS).map(([name, pos]) => (
            <Box key={name} pos={pos} container={containerByName[name]} name={name} />
          ))}
        </svg>
      </div>

      <div style={{ marginTop: 12, fontSize: 12, color: "#666", textAlign: "center" }}>
        Flèches : <code>A → B</code> signifie « <strong>B utilise A</strong> ».
        Cf. <code>docs/container_deps.md</code> pour le graphe complet (incluant les edges secondaires).
      </div>
    </div>
  );
}

function Box({
  pos, container, name,
}: {
  pos: { x: number; y: number };
  container: Container | undefined;
  name: string;
}): JSX.Element {
  const status = container?.status ?? "DOWN";
  const stroke =
    status === "OK" ? "#6c6" :
    status === "STALE" ? "#cc6" : "#e66";
  return (
    <g>
      <rect
        x={pos.x}
        y={pos.y}
        width={BOX_W}
        height={BOX_H}
        rx={4}
        fill="#1a1a1a"
        stroke={stroke}
        strokeWidth={2}
      />
      <text x={pos.x + BOX_W / 2} y={pos.y + 22} textAnchor="middle" fill="#fff" fontSize="13" fontWeight="600">
        {name}
      </text>
      <text x={pos.x + BOX_W / 2} y={pos.y + 38} textAnchor="middle" fill="#888" fontSize="10" fontFamily="Consolas, monospace">
        {(container?.image ?? "—").split("/").pop()}
      </text>
      <text x={pos.x + BOX_W / 2} y={pos.y + 53} textAnchor="middle" fill={stroke} fontSize="11" fontWeight="600">
        ● {status}
      </text>
    </g>
  );
}

function Arrow({
  from, to,
}: {
  from: { x: number; y: number } | undefined;
  to: { x: number; y: number } | undefined;
}): JSX.Element | null {
  if (!from || !to) return null;
  // Arrow va du bord bas/haut de la box source au bord haut/bas de la cible.
  // On calcule les centres et on raccourcit pour ne pas chevaucher la box.
  const fcx = from.x + BOX_W / 2;
  const fcy = from.y + BOX_H / 2;
  const tcx = to.x + BOX_W / 2;
  const tcy = to.y + BOX_H / 2;
  // Compute clipping aux bords des box (rectangles).
  const start = clipToBox(fcx, fcy, tcx, tcy);
  const end = clipToBox(tcx, tcy, fcx, fcy);
  return (
    <line
      x1={start.x}
      y1={start.y}
      x2={end.x}
      y2={end.y}
      stroke="#555"
      strokeWidth={1.2}
      markerEnd="url(#arrow)"
    />
  );
}

/**
 * Clip une ligne partant de (cx, cy) — supposé être le centre d'une box BOX_W
 * × BOX_H — en direction de (tx, ty) pour qu'elle s'arrête au bord du rect.
 */
function clipToBox(cx: number, cy: number, tx: number, ty: number): { x: number; y: number } {
  const dx = tx - cx;
  const dy = ty - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const hw = BOX_W / 2;
  const hh = BOX_H / 2;
  const tx_ = dx !== 0 ? hw / Math.abs(dx) : Infinity;
  const ty_ = dy !== 0 ? hh / Math.abs(dy) : Infinity;
  const t = Math.min(tx_, ty_);
  return { x: cx + dx * t, y: cy + dy * t };
}

const layerLabelSvg = {
  fill: "#7af",
  fontSize: 10,
  fontFamily: "system-ui, sans-serif",
  letterSpacing: 1.5,
} as const;

const btnStyle = {
  padding: "4px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};
