/**
 * Stack Overview — draw.io-style SVG diagram of the 10 containers +
 * dependency arrows, live colored status. Auto-refresh 5s.
 *
 * Layout, 4 rows:
 *   Row 1 (data)    : redis · postgres · ib-gateway
 *   Row 2 (engines) : market-data · vol-engine · risk-engine · db-writer
 *   Row 3 (server)  : api
 *   Row 4 (edge)    : nginx · frontend
 *
 * Arrow convention: A → B means "B uses A" (cf. container_deps.md).
 * The engines sit in the middle because they are the central business layer.
 */
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../../api/client";

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

const POLL_MS = 3_000;

// Coordinates (x, y) in SVG units. ViewBox "0 0 W H".
const BOX_W = 150;
const BOX_H = 85;
const SVG_W = 900;
const SVG_H = 770;

// Shape per container : cylinder for stores (postgres / redis / loki /
// prometheus), cloud for the external IB Gateway, screen-with-stand for
// the operator-facing UIs (frontend / grafana), plain rect for everything
// else (engines / api / execution / nginx / collectors).
const SHAPES: Record<string, "rect" | "cylinder" | "cloud" | "screen"> = {
  postgres: "cylinder",
  redis: "cylinder",
  loki: "cylinder",
  prometheus: "cylinder",
  "ib-gateway": "cloud",
  frontend: "screen",
  grafana: "screen",
};

// Layer grouping panels (transparent fill, dashed colored border).
// Drawn first so all boxes overlay them.
// Per-panel padding (inside) = 15 px ; inter-panel gap = 15 px.
const LAYER_PANELS: { name: string; color: string; y: number; h: number }[] = [
  { name: "DATA",    color: "#5b8fd8", y: 15,  h: 115 },  // row 1 (y=30..115)
  { name: "ENGINES", color: "#7ac26b", y: 145, h: 115 },  // row 2 (y=160..245)
  { name: "APP",     color: "#d8a35b", y: 275, h: 115 },  // row 3 (y=290..375)
  { name: "EDGE",    color: "#b07acc", y: 405, h: 115 },  // row 4 (y=420..505)
  { name: "OBS",     color: "#c2a93a", y: 535, h: 215 },  // rows 5+6 (y=550..735)
];

// 4-column grid centred on SVG_W/2 = 450 :
//   col1 x=75  | col2 x=275 | col3 x=475 | col4 x=675
//   margin gauche = 75  ;  margin droite = 75  (BOX_W=150 → rightmost+BOX_W=825)
//   gap inter-box = 50  (entre x+150 et x_suivant)
//
// Symmetric placement rules :
//   - 4-box row → x = col1, col2, col3, col4
//   - 3-box row → x = col1, centre(375), col4
//   - 2-box row → x = col2, col3
const POSITIONS: Record<string, { x: number; y: number }> = {
  // Row 1 (data sources, 3 boxes) — panel DATA (y=15..130)
  "redis":      { x: 75,  y: 30 },
  "postgres":   { x: 375, y: 30 },
  "ib-gateway": { x: 675, y: 30 },
  // Row 2 (engines, 4 boxes) — panel ENGINES (y=145..260)
  "market-data": { x: 75,  y: 160 },
  "vol-engine":  { x: 275, y: 160 },
  "risk-engine": { x: 475, y: 160 },
  "db-writer":   { x: 675, y: 160 },
  // Row 3 (app, 2 boxes) — panel APP (y=275..390)
  "api":         { x: 275, y: 290 },
  "execution":   { x: 475, y: 290 },
  // Row 4 (edge, 2 boxes) — panel EDGE (y=405..520)
  "nginx":       { x: 275, y: 420 },
  "frontend":    { x: 475, y: 420 },
  // Row 5 (obs collector, 1 box centred) — panel OBS (y=535..750).
  // tempo + otel are dev-only (profil `traces`) and intentionally absent.
  "promtail":   { x: 375, y: 550 },
  // Row 6 (obs stores + UI, 3 boxes) — same panel
  "loki":       { x: 75,  y: 650 },
  "prometheus": { x: 375, y: 650 },
  "grafana":    { x: 675, y: 650 },
};

// Edges = (from, to). "from" is used by "to" (e.g. redis → api = api uses redis).
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
  // data → api / execution
  { from: "redis",    to: "api" },
  { from: "postgres", to: "api" },
  { from: "postgres", to: "execution" },
  { from: "ib-gateway", to: "execution" },
  // api → execution (proxy forward)
  { from: "api",      to: "execution" },
  // → nginx (edge)
  { from: "api",      to: "nginx" },
  { from: "frontend", to: "nginx" },
  // Observability flows (kept minimal to avoid spaghetti)
  { from: "promtail",       to: "loki" },        // Docker logs → Loki
  { from: "loki",           to: "grafana" },
  { from: "prometheus",     to: "grafana" },
];

export function StackOverview(): JSX.Element {
  const [data, setData] = useState<StackResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const fetchData = async () => {
    setError(null);
    try {
      const r = await apiFetch("/api/v1/dev/stack");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void fetchData();
    timerRef.current = window.setInterval(fetchData, POLL_MS);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, []);

  const containerByName: Record<string, Container> = {};
  if (data) for (const c of data.containers) containerByName[c.name] = c;

  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <span style={{ color: "#666", fontSize: 11 }}>auto {POLL_MS / 1000}s</span>
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

          {/* Layer grouping panels (transparent, drawn behind everything) */}
          {LAYER_PANELS.map((p) => (
            <g key={p.name}>
              <rect
                x={20}
                y={p.y}
                width={SVG_W - 40}
                height={p.h}
                rx={8}
                fill={p.color}
                fillOpacity={0.05}
                stroke={p.color}
                strokeOpacity={0.4}
                strokeDasharray="4,4"
                strokeWidth={1}
              />
              <text
                x={28}
                y={p.y + 14}
                fill={p.color}
                fontSize={9}
                fontFamily="system-ui, sans-serif"
                letterSpacing={2}
                fontWeight={700}
              >
                {p.name}
              </text>
            </g>
          ))}

          {/* Edges (drawn before boxes so the boxes overlay them) */}
          {EDGES.map((e, i) => (
            <Arrow key={i} from={POSITIONS[e.from]} to={POSITIONS[e.to]} />
          ))}

          {/* Boxes */}
          {Object.entries(POSITIONS).map(([name, pos]) => (
            <Box key={name} pos={pos} container={containerByName[name]} name={name} />
          ))}
        </svg>
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
  const shape = SHAPES[name] ?? "rect";
  return (
    <g>
      <ShapeBg shape={shape} x={pos.x} y={pos.y} stroke={stroke} />
      <text x={pos.x + BOX_W / 2} y={pos.y + 30} textAnchor="middle"
            fill="#fff" fontSize="13" fontWeight="600">
        {name}
      </text>
      <text x={pos.x + BOX_W / 2} y={pos.y + 53} textAnchor="middle"
            fill="#bbb" fontSize="10" fontFamily="Consolas, monospace">
        {(container?.image ?? "—").split("/").pop()}
      </text>
      <text x={pos.x + BOX_W / 2} y={pos.y + 73} textAnchor="middle"
            fill={stroke} fontSize="11" fontWeight="600">
        ● {status}
      </text>
    </g>
  );
}


/** Render the container's shape — body only, no text. */
function ShapeBg({
  shape, x, y, stroke,
}: {
  shape: "rect" | "cylinder" | "cloud" | "screen";
  x: number; y: number; stroke: string;
}): JSX.Element {
  const fill = "#1a1a1a";
  const sw = 2;
  if (shape === "cylinder") {
    // DB cylinder : full top ellipse + side lines + front bottom arc.
    const ry = 7;
    const W = BOX_W;
    const H = BOX_H;
    return (
      <g>
        {/* Body fill rectangle (between the ellipses) */}
        <rect x={x} y={y + ry} width={W} height={H - 2 * ry}
              fill={fill} stroke="none" />
        {/* Side verticals */}
        <line x1={x} y1={y + ry} x2={x} y2={y + H - ry}
              stroke={stroke} strokeWidth={sw} />
        <line x1={x + W} y1={y + ry} x2={x + W} y2={y + H - ry}
              stroke={stroke} strokeWidth={sw} />
        {/* Top ellipse (full) */}
        <ellipse cx={x + W / 2} cy={y + ry} rx={W / 2} ry={ry}
                 fill={fill} stroke={stroke} strokeWidth={sw} />
        {/* Bottom front arc */}
        <path d={`M ${x} ${y + H - ry} A ${W / 2} ${ry} 0 0 0 ${x + W} ${y + H - ry}`}
              fill="none" stroke={stroke} strokeWidth={sw} />
      </g>
    );
  }
  if (shape === "cloud") {
    // Stylised cloud : 3 bumps on top, flat-ish bottom.
    const W = BOX_W, H = BOX_H;
    const d = [
      `M ${x + 12} ${y + H - 6}`,
      `C ${x} ${y + H - 6} ${x} ${y + 20} ${x + 18} ${y + 18}`,
      `C ${x + 18} ${y + 2} ${x + 50} ${y + 0} ${x + 58} ${y + 16}`,
      `C ${x + 64} ${y + 0} ${x + 95} ${y + 0} ${x + 100} ${y + 18}`,
      `C ${x + W - 12} ${y + 4} ${x + W} ${y + 28} ${x + W - 18} ${y + 30}`,
      `C ${x + W + 2} ${y + H - 12} ${x + W - 20} ${y + H - 4} ${x + W - 28} ${y + H - 6}`,
      `L ${x + 12} ${y + H - 6}`,
      `Z`,
    ].join(" ");
    return <path d={d} fill={fill} stroke={stroke} strokeWidth={sw} />;
  }
  if (shape === "screen") {
    // Monitor : rounded screen + tilted stand foot.
    const screenH = BOX_H - 10;
    const cx = x + BOX_W / 2;
    return (
      <g>
        <rect x={x} y={y} width={BOX_W} height={screenH} rx={4}
              fill={fill} stroke={stroke} strokeWidth={sw} />
        {/* Stand : two converging lines + base bar */}
        <line x1={cx - 8} y1={y + screenH} x2={cx - 14} y2={y + screenH + 8}
              stroke={stroke} strokeWidth={sw} />
        <line x1={cx + 8} y1={y + screenH} x2={cx + 14} y2={y + screenH + 8}
              stroke={stroke} strokeWidth={sw} />
        <line x1={cx - 18} y1={y + screenH + 9} x2={cx + 18} y2={y + screenH + 9}
              stroke={stroke} strokeWidth={sw} strokeLinecap="round" />
      </g>
    );
  }
  // Default : rounded rectangle.
  return (
    <rect x={x} y={y} width={BOX_W} height={BOX_H} rx={4}
          fill={fill} stroke={stroke} strokeWidth={sw} />
  );
}

function Arrow({
  from, to,
}: {
  from: { x: number; y: number } | undefined;
  to: { x: number; y: number } | undefined;
}): JSX.Element | null {
  if (!from || !to) return null;
  // The arrow goes from the bottom/top edge of the source box to the
  // top/bottom edge of the target. Compute the centers then shorten the
  // line so it does not overlap the box.
  const fcx = from.x + BOX_W / 2;
  const fcy = from.y + BOX_H / 2;
  const tcx = to.x + BOX_W / 2;
  const tcy = to.y + BOX_H / 2;
  // Compute clipping at the box edges (rectangles).
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
 * Clip a line starting at (cx, cy) — assumed to be the center of a BOX_W ×
 * BOX_H box — towards (tx, ty) so that it stops at the edge of the rect.
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


