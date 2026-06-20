/**
 * Dev "Pipeline" tab — live end-to-end plumbing schematic per PROD panel.
 * Faithful to the Claude Design mockup (Ticker bid/ask). Always live (no toggle):
 * the schema mounts the desk DataProvider and reads real state.
 *
 * Spot ticker = the REAL pipeline: each block resolves its actual health from the
 * `system` domain (market-data heartbeat, IB Gateway connection, redis/api status)
 * + the WS leg from the ticks feed freshness; a pipe flows only between two healthy
 * blocks, a down block goes red with a ✕. The Ticker terminal shows live bid/ask/mid
 * from /ws/ticks. Other panels fall back to their domain freshness (uniform) until
 * wired the same way.
 */
import dagre from "@dagrejs/dagre";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useWsLog } from "../../hooks/useWsLog";
import { DataProvider } from "../../voldesk/data/provider";
import { type SystemData, useDeskData } from "../../voldesk/data/deskData";
import { DashboardView } from "../../voldesk/views/DashboardView";
import { PortfolioView } from "../../voldesk/views/PortfolioView";
import { RiskView } from "../../voldesk/views/RiskView";
import { SignalsView } from "../../voldesk/views/SignalsView";
import { TradeView } from "../../voldesk/views/TradeView";
import { PIPELINES, type DagNode, type DomainId, type PanelPipe, type PipeDag, type PipeNode, type Role, type ViewId } from "./pipelines";

// The real prod view rendered in the terminal "screen" (the panel lives in it).
// Dashboard + Trade take props in the app; stub them for the viz.
const VIEW_COMPONENTS: Record<ViewId, () => JSX.Element> = {
  dashboard: () => <DashboardView go={() => undefined} />,
  trade: () => <TradeView tweaks={{ density: "comfortable", showGreeks: true }} />,
  signals: SignalsView,
  risk: RiskView,
  portfolio: PortfolioView,
};

const GREEN = "#3ec46d", AMBER = "#d9a441", RED = "#e0564f";
type H = "up" | "warn" | "down";
const HCOLOR: Record<H, string> = { up: GREEN, warn: AMBER, down: RED };

const VIEW_LABEL: Record<ViewId, string> = {
  dashboard: "Dashboard", trade: "Trade", signals: "Signal", risk: "Risk", portfolio: "Portfolio",
};
// Each block's single data-flow archetype → tag term, glyph and colour. The
// term replaces the old CONTAINER/EXTERNAL tag; the glyph changes with it.
const ROLE_META: Record<Role, { term: string; glyph: string; label: string; color: string }> = {
  emit: { term: "EMITTER", glyph: "➚", label: "emitter · sends data out", color: "#3ec46d" },
  transform: { term: "TRANSFORMER", glyph: "⚙", label: "transformer · computes / transforms the data", color: "#5b8fd6" },
  receive: { term: "RECEIVER", glyph: "➘", label: "receiver · receives / records the data", color: "#d9a441" },
  hub: { term: "HUB", glyph: "❖", label: "hub · centralizes inbound + redistributes outbound", color: "#a77bd6" },
};

const hexa = (hex: string, a: number): string => {
  const c = hex.replace("#", "");
  return `rgba(${parseInt(c.slice(0, 2), 16)},${parseInt(c.slice(2, 4), 16)},${parseInt(c.slice(4, 6), 16)},${a})`;
};
const clk = (d: Date): string => {
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

function normStatus(s: string | undefined): H {
  const x = String(s ?? "").toLowerCase();
  if (x.startsWith("up") || x === "ok" || x === "healthy" || x === "live") return "up";
  if (x.startsWith("warn") || x === "degraded" || x.startsWith("stale")) return "warn";
  return "down";
}
function buildHealthMap(system: SystemData | null): Record<string, H> {
  const m: Record<string, H> = {};
  if (!system) return m;
  for (const e of system.engines) m[e.name] = normStatus(e.status);
  for (const layer of system.stack) for (const it of layer.items) m[it.name] = normStatus(it.status);
  return m;
}
function resolve(node: PipeNode, hmap: Record<string, H>, domain: H, ws: H, api: H): H {
  if (node.health === "__self") return "up";
  if (node.health === "__ws") return ws;
  if (node.health === "__api") return api; // api-specific: up if it responds (not the global DEGRADED)
  if (node.health && hmap[node.health]) return hmap[node.health]!;
  return domain; // fallback: panel's domain freshness
}

const CSS = `
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
@keyframes ppflow { from{background-position:0 0} to{background-position:16px 0} }
@keyframes ppdash { to{stroke-dashoffset:-36} }
@keyframes pppulse { 0%,100%{opacity:1} 50%{opacity:.4} }
@keyframes pphalo {
  0%,100%{box-shadow:0 0 0 1px rgba(62,196,109,.22),0 0 22px rgba(62,196,109,.16),inset 0 0 30px rgba(62,196,109,.05)}
  50%{box-shadow:0 0 0 1px rgba(62,196,109,.32),0 0 32px rgba(62,196,109,.28),inset 0 0 34px rgba(62,196,109,.08)}
}
.pp-root{height:calc(100vh - 92px);display:flex;overflow:hidden;background:#0e0e0e;color:#d4d8e0;font-family:'IBM Plex Sans',system-ui,sans-serif}
.pp-root *{box-sizing:border-box}
.pp-mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
.pp-main{flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden}
.pp-side{flex:none;width:276px;background:#08090b;border-right:1px solid #1b1e25;display:flex;flex-direction:column;overflow:hidden}
.pp-side-list{flex:1;overflow-y:auto;overflow-x:hidden;padding:4px 0 14px}
.pp-side-list::-webkit-scrollbar{width:8px}
.pp-side-list::-webkit-scrollbar-thumb{background:#20242d;border-radius:5px}
.pp-grp{font-size:12.5px;font-weight:700;letter-spacing:.1em;color:#dfe4ec;padding:16px 16px 7px;border-top:1px solid #15181e}
.pp-side-list>div:first-child .pp-grp{border-top:none}
.pp-item{display:flex;align-items:center;gap:9px;padding:8px 14px 8px 16px;cursor:pointer;border-left:2px solid transparent;transition:background .12s}
.pp-item:hover{background:#11141a}
.pp-item.on{background:#141a16;border-left-color:#3ec46d}
.pp-item-name{flex:1;font-size:12.5px;color:#9aa1ae;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pp-item.on .pp-item-name{color:#e6f6ec}
.pp-dot{flex:none;width:9px;height:9px;border-radius:50%}
.pp-canvas{flex:1;position:relative;overflow:hidden;background:radial-gradient(ellipse 90% 130% at 50% 0%, #121620 0%, #0e0e0e 62%);cursor:grab}
.pp-canvas.grabbing{cursor:grabbing}
.pp-stagewrap{position:absolute;top:0;left:0;transform-origin:0 0}
.pp-area::-webkit-scrollbar{height:9px;width:9px}
.pp-area::-webkit-scrollbar-thumb{background:#262a33;border-radius:5px}
`;

function XMark({ size }: { size: number }): JSX.Element {
  return <svg width={size} height={size} viewBox="0 0 28 28" fill="none"><path d="M7 7L21 21M21 7L7 21" stroke={RED} strokeWidth="3" strokeLinecap="round" /></svg>;
}

// hexagon (pointy left/right) for EXTERNAL blocks — a distinct shape so the
// broker reads as "outside our infra", unlike the rounded-rect containers.
const HEX = "polygon(18px 0, calc(100% - 18px) 0, 100% 50%, calc(100% - 18px) 100%, 18px 100%, 0 50%)";

function Block({ node, status, tip, onEnter, onLeave }: {
  node: PipeNode; status: H; tip: boolean; onEnter: () => void; onLeave: () => void;
}): JSX.Element {
  const rm = ROLE_META[node.role ?? "receive"];
  const sc = HCOLOR[status];
  const down = status === "down";
  const external = node.kind === "external";
  const cardBg = down ? "#1b1517" : "#181b22";

  const body = (
    <>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: hexa(sc, down ? 0.7 : 0.55) }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div className="pp-mono" style={{ width: 30, height: 30, borderRadius: 6, display: "grid", placeItems: "center", fontSize: 17, color: rm.color, border: `1px solid ${hexa(rm.color, 0.42)}`, background: hexa(rm.color, 0.1), opacity: down ? 0.6 : 1 }}>{rm.glyph}</div>
        <div className="pp-mono" title={rm.label} style={{ fontSize: 10.5, letterSpacing: ".1em", color: rm.color, fontWeight: 700, opacity: down ? 0.7 : 1 }}>{rm.term}</div>
      </div>
      <div style={{ fontSize: 16.5, fontWeight: 600, color: "#e6eaf1" }}>{node.label}</div>
      <div className="pp-mono" style={{ fontSize: 12, color: "#6b7180", lineHeight: 1.45 }}>{node.sub}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ width: 9, height: 9, borderRadius: "50%", background: sc, boxShadow: `0 0 8px ${hexa(sc, 0.8)}`, animation: status === "up" ? "pppulse 1.6s ease-in-out infinite" : "none" }} />
        <span className="pp-mono" style={{ fontSize: 11.5, letterSpacing: ".12em", fontWeight: 600, color: sc }}>{status === "up" ? "UP" : status === "warn" ? "WARN" : "DOWN"}</span>
      </div>
    </>
  );

  return (
    <div style={{ flex: "none", width: external ? 210 : 188, position: "relative" }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      {down ? <div style={{ position: "absolute", left: "50%", top: -42, transform: "translateX(-50%)", lineHeight: 0, zIndex: 6 }}><XMark size={34} /></div> : null}
      {external ? (
        // two-layer clip = a bordered hexagon (CSS borders don't follow clip-path)
        <div style={{ clipPath: HEX, background: hexa(sc, down ? 0.5 : 0.4), padding: 1.5, boxShadow: down ? `0 0 16px ${hexa(RED, 0.14)}` : `0 0 18px ${hexa(rm.color, 0.1)}` }}>
          <div style={{ position: "relative", clipPath: HEX, background: cardBg, padding: "15px 30px 16px", minHeight: 150, display: "flex", flexDirection: "column", gap: 10, overflow: "hidden" }}>
            {body}
          </div>
        </div>
      ) : (
        <div style={{ position: "relative", background: cardBg, border: `1px solid ${hexa(sc, down ? 0.5 : 0.32)}`, borderRadius: 7, padding: "15px 15px 16px", minHeight: 150, display: "flex", flexDirection: "column", gap: 10, overflow: "hidden", boxShadow: down ? `0 0 16px ${hexa(RED, 0.14)}, inset 0 0 22px ${hexa(RED, 0.05)}` : "none", transition: "background .25s, border-color .25s, box-shadow .25s" }}>
          {body}
        </div>
      )}
      {tip ? (
        <div className="pp-mono" style={{ position: "absolute", top: "calc(100% + 9px)", left: "50%", transform: "translateX(-50%)", whiteSpace: "nowrap", background: "#20242d", border: "1px solid #313742", borderRadius: 6, padding: "6px 9px", fontSize: 10, color: "#c3c8d2", boxShadow: "0 8px 22px rgba(0,0,0,.5)", zIndex: 15 }}>
          {rm.term}: {node.label} — {node.sub}
        </div>
      ) : null}
    </div>
  );
}

function Pipe({ label, state, hover, onEnter, onLeave }: {
  label: string; state: "flow" | "warn" | "down"; hover: boolean; onEnter: () => void; onLeave: () => void;
}): JSX.Element {
  const sc = state === "flow" ? GREEN : state === "warn" ? AMBER : RED;
  const animate = state !== "down"; // warn still flows (degraded but up) — only down breaks it
  return (
    <div style={{ position: "relative", flex: 1, minWidth: 156, alignSelf: "stretch", display: "flex", alignItems: "center", padding: "0 1px" }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <div className="pp-mono" style={{ position: "absolute", top: "calc(50% - 27px)", left: "50%", transform: "translateX(-50%)", whiteSpace: "nowrap", fontSize: 11.5, letterSpacing: ".07em", textTransform: "uppercase", pointerEvents: "none", color: hover ? "#dfe7e2" : "#7b8494", textShadow: hover ? `0 0 9px ${hexa(sc, 0.55)}` : "none", fontWeight: hover ? 600 : 500 }}>{label}</div>
      <div style={{ position: "relative", flex: 1, height: 10, minWidth: 30, background: "#0c0e12", border: `1px solid ${hexa(sc, 0.42)}`, borderRadius: 6, overflow: "hidden", boxShadow: animate ? `0 0 10px ${hexa(sc, 0.16)}, inset 0 0 6px ${hexa(sc, 0.1)}` : `inset 0 0 6px ${hexa(sc, 0.06)}`, transition: "box-shadow .35s, border-color .35s" }}>
        <div style={{ position: "absolute", inset: 0, backgroundImage: `repeating-linear-gradient(90deg, ${sc} 0, ${sc} 6px, ${hexa(sc, 0)} 6px, ${hexa(sc, 0)} 16px)`, backgroundSize: "16px 100%", animation: animate ? "ppflow .6s linear infinite" : "none", opacity: animate ? 0.9 : 0.45, transition: "opacity .35s" }} />
      </div>
      <div style={{ position: "absolute", right: -3, top: "50%", transform: "translateY(-50%)", lineHeight: 0 }}>
        <svg width="12" height="15" viewBox="0 0 9 11" fill="none"><path d="M1.5 1.5L6 5.5L1.5 9.5" stroke={sc} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </div>
    </div>
  );
}

// EURUSD spot ticker — bespoke terminal screen (no real Dashboard `Panel` to
// isolate: bid/ask lives in the app header). Reads the live /ws/ticks feed.
function TickerScreen({ live }: { live: boolean }): JSX.Element {
  const { ticks } = useDeskData();
  const d = ticks.data;
  const bid = d?.bid;
  const ask = d?.ask;
  const mid = d?.mid ?? (bid != null && ask != null ? (bid + ask) / 2 : undefined);
  const f5 = (v?: number): string => (v != null ? v.toFixed(5) : "—.—————");
  const accent = live ? GREEN : AMBER;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 14, minHeight: 380, padding: 30 }}>
      <div className="pp-mono" style={{ fontSize: 12, letterSpacing: ".24em", color: "#8a909c", textTransform: "uppercase" }}>EURUSD spot</div>
      <div className="pp-mono" style={{ fontSize: 58, fontWeight: 600, lineHeight: 1, color: live ? "#eef6f0" : "#cdd1d8", textShadow: live ? `0 0 22px ${hexa(GREEN, 0.4)}` : "none", animation: live ? "pphalo 2.6s ease-in-out infinite" : "none" }}>{f5(mid)}</div>
      <div style={{ display: "flex", alignItems: "stretch", gap: 24, marginTop: 8 }}>
        <div style={{ textAlign: "center" }}>
          <div className="pp-mono" style={{ fontSize: 10, letterSpacing: ".18em", color: "#a06158" }}>BID</div>
          <div className="pp-mono" style={{ fontSize: 26, fontWeight: 500, color: "#e0726a" }}>{f5(bid)}</div>
        </div>
        <div style={{ width: 1, background: "#262a33" }} />
        <div style={{ textAlign: "center" }}>
          <div className="pp-mono" style={{ fontSize: 10, letterSpacing: ".18em", color: "#4f9c74" }}>ASK</div>
          <div className="pp-mono" style={{ fontSize: 26, fontWeight: 500, color: "#5fce93" }}>{f5(ask)}</div>
        </div>
      </div>
      <div className="pp-mono" style={{ fontSize: 10.5, color: accent, marginTop: 10 }}>{live ? "● live · /ws/ticks" : "○ stale · last value"}</div>
    </div>
  );
}

function Terminal({ pipe, live }: { pipe: PanelPipe; live: boolean }): JSX.Element {
  const accent = live ? GREEN : AMBER;
  const isTicker = pipe.id === "ticker";
  const ViewComp = VIEW_COMPONENTS[pipe.view];
  const screenRef = useRef<HTMLDivElement>(null);

  // Isolate the selected panel in JS (no `:has()` — that selector silently
  // drops the whole rule on parsers that don't support it, which left every
  // value showing the full view). Hide every annotated panel that is neither
  // the target nor an ancestor of it; leave the target and its ancestors
  // untouched so the panel keeps its EXACT live design/size (no forced
  // block/width — that distorted it and caused a scrollbar-width oscillation
  // that made busy panels vibrate). Re-applied on every live re-render via a
  // MutationObserver (React reconciles the view ~1-2s on fresh data).
  useLayoutEffect(() => {
    const root = screenRef.current;
    if (!root || !pipe.isolated) return;
    const apply = (): void => {
      const target = root.querySelector<HTMLElement>(`[data-pp="${window.CSS.escape(pipe.id)}"]`);
      const tagged = root.querySelectorAll<HTMLElement>("[data-pp]");
      tagged.forEach((el) => {
        if (!target || el === target || el.contains(target)) el.style.removeProperty("display");
        else el.style.setProperty("display", "none", "important");
      });
    };
    apply();
    const obs = new MutationObserver(apply);
    obs.observe(root, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, [pipe.id, pipe.isolated]);

  return (
    <div style={{ flex: "none", alignSelf: "stretch", width: 640, minHeight: 420, display: "flex", flexDirection: "column", border: `1px solid ${hexa(accent, live ? 0.55 : 0.5)}`, borderRadius: 9, overflow: "hidden", background: "#0f1115", boxShadow: live ? "0 0 0 1px rgba(62,196,109,.25),0 0 28px rgba(62,196,109,.18)" : "inset 0 0 26px rgba(217,164,65,.05)", animation: live ? "pphalo 2.6s ease-in-out infinite" : "none" }}>
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 9, padding: "9px 12px", borderBottom: `1px solid ${hexa(accent, 0.3)}`, background: "#13171c" }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: accent, boxShadow: `0 0 7px ${hexa(accent, 0.85)}`, animation: live ? "pppulse 1.4s ease-in-out infinite" : "none" }} />
        <span style={{ fontSize: 14, fontWeight: 600, color: "#eef1f6" }}>{pipe.panel}</span>
        <span className="pp-mono" style={{ fontSize: 9.5, color: live ? "#7fcf9a" : "#d9b86a" }}>{live ? "live" : "stale"}</span>
        <span style={{ flex: 1 }} />
        <span className="pp-mono" style={{ fontSize: 9.5, letterSpacing: ".14em", color: accent, fontWeight: 600 }}>{pipe.isolated || isTicker ? "PANEL" : "VIEW"} · {VIEW_LABEL[pipe.view]}</span>
      </div>
      <div ref={screenRef} className={pipe.isolated ? "pp-screen pp-iso" : "pp-screen"} style={{ flex: 1, overflow: "auto", minHeight: 0, background: "#0f1115" }}>
        {isTicker ? <TickerScreen live={live} /> : <ViewComp />}
      </div>
    </div>
  );
}

// roll a pipe's per-node statuses into one health pill: any down → down,
// any warn → warn, else up.
function rollUp(statuses: H[]): H {
  return statuses.includes("down") ? "down" : statuses.includes("warn") ? "warn" : "up";
}

function Sidebar({ id, setId, healthById }: {
  id: string; setId: (v: string) => void; healthById: Record<string, H>;
}): JSX.Element {
  const byView = new Map<ViewId, PanelPipe[]>();
  for (const p of PIPELINES) byView.set(p.view, [...(byView.get(p.view) ?? []), p]);
  const downCount = PIPELINES.filter((p) => healthById[p.id] === "down").length;
  return (
    <div className="pp-side">
      <div style={{ flex: "none", padding: "13px 16px 12px", borderBottom: "1px solid #1b1e25" }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#e6eaf1" }}>Pipelines</div>
        <div className="pp-mono" style={{ fontSize: 10, color: downCount ? "#e08a84" : "#5f8d6e", marginTop: 3 }}>
          {PIPELINES.length} panels · {downCount ? `${downCount} degraded` : "all live"}
        </div>
      </div>
      <div className="pp-side-list">
        {[...byView.entries()].map(([view, panels]) => (
          <div key={view}>
            <div className="pp-grp pp-mono">{VIEW_LABEL[view].toUpperCase()}</div>
            {panels.map((p) => {
              const h = healthById[p.id] ?? "down";
              const c = HCOLOR[h];
              return (
                <div key={p.id} className={"pp-item" + (p.id === id ? " on" : "")} onClick={() => setId(p.id)}>
                  <span className="pp-item-name">{p.panel}</span>
                  <span className="pp-dot" style={{ background: c, boxShadow: `0 0 7px ${hexa(c, 0.8)}`, animation: h === "up" ? "pppulse 1.8s ease-in-out infinite" : "none" }} />
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

// One SVG connector: a dim base path + an animated dashed path on top. Flow
// runs (dashes move) unless the edge is down.
function FlowPath({ d, state }: { d: string; state: "flow" | "warn" | "down" }): JSX.Element {
  const sc = state === "flow" ? GREEN : state === "warn" ? AMBER : RED;
  const animate = state !== "down";
  return (
    <g>
      <path d={d} fill="none" stroke={hexa(sc, 0.22)} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
      <path d={d} fill="none" stroke={sc} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" strokeDasharray="7 11" style={{ animation: animate ? "ppdash .7s linear infinite" : "none", opacity: animate ? 0.95 : 0.4 }} />
    </g>
  );
}

// Full data-flow DAG, dagre-laid-out (left→right). Every real input/output is
// an edge, so shared dual-role nodes (Postgres written by db-writer AND read by
// the api) and fan-out hubs (Redis → persist + serve) render faithfully.
function DagSchema({ dag, pipe, resolveNode }: {
  dag: PipeDag; pipe: PanelPipe; resolveNode: (n: PipeNode) => H;
}): JSX.Element {
  const [hover, setHover] = useState<string | null>(null);
  const sizeOf = (n: DagNode): { w: number; h: number } =>
    n.terminal ? { w: 640, h: 460 } : n.kind === "external" ? { w: 210, h: 178 } : { w: 188, h: 178 };

  const layout = useMemo(() => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 56, ranksep: 120, marginx: 14, marginy: 14 });
    g.setDefaultEdgeLabel(() => ({}));
    dag.nodes.forEach((n) => { const s = sizeOf(n); g.setNode(n.id, { width: s.w, height: s.h }); });
    dag.edges.forEach((e) => g.setEdge(e.from, e.to));
    dagre.layout(g);
    return g;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dag]);

  const gw = (layout.graph().width as number) ?? 0;
  const gh = (layout.graph().height as number) ?? 0;
  const byId: Record<string, DagNode> = {};
  dag.nodes.forEach((n) => { byId[n.id] = n; });
  const pstate = (a: H, b: H): "flow" | "warn" | "down" =>
    a === "down" || b === "down" ? "down" : a === "up" && b === "up" ? "flow" : "warn";
  const pts = (e: { from: string; to: string }): { x: number; y: number }[] =>
    (layout.edge(e.from, e.to) as { points: { x: number; y: number }[] }).points;
  const toPath = (p: { x: number; y: number }[]): string =>
    p.map((q, i) => `${i === 0 ? "M" : "L"} ${q.x.toFixed(1)} ${q.y.toFixed(1)}`).join(" ");

  return (
    <div style={{ position: "relative", width: gw, height: gh }}>
      <svg width={gw} height={gh} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
        {dag.edges.map((e, i) => <FlowPath key={i} d={toPath(pts(e))} state={pstate(resolveNode(byId[e.from]!), resolveNode(byId[e.to]!))} />)}
      </svg>
      {dag.edges.map((e, i) => {
        if (!e.label) return null;
        const p = pts(e);
        const mid = p[Math.floor(p.length / 2)]!;
        return (
          <div key={i} className="pp-mono" style={{ position: "absolute", left: mid.x, top: mid.y, transform: "translate(-50%,-50%)", fontSize: 10.5, letterSpacing: ".04em", textTransform: "uppercase", color: "#7b8494", background: "#0e0e0e", padding: "1px 5px", borderRadius: 4, whiteSpace: "nowrap", pointerEvents: "none" }}>{e.label}</div>
        );
      })}
      {dag.nodes.map((n) => {
        const gn = layout.node(n.id) as { x: number; y: number };
        const s = sizeOf(n);
        const left = gn.x - s.w / 2, top = gn.y - s.h / 2;
        return n.terminal ? (
          <div key={n.id} style={{ position: "absolute", left, top, width: s.w, height: s.h, display: "flex" }} onMouseDown={(ev) => ev.stopPropagation()}>
            <Terminal pipe={pipe} live={resolveNode(n) === "up"} />
          </div>
        ) : (
          <div key={n.id} style={{ position: "absolute", left, top }}>
            <Block node={n} status={resolveNode(n)} tip={hover === n.id} onEnter={() => setHover(n.id)} onLeave={() => setHover(null)} />
          </div>
        );
      })}
    </div>
  );
}

// The panel's domain → the WS channel that carries its live payload (folded in
// from the removed WS-monitor tab). REST-only domains (system/config) → null.
const WS_BASE = (import.meta.env["VITE_WS_BASE_URL"] as string | undefined) ?? "";
function wsChannelFor(domain: DomainId): { url: string; label: string } | null {
  if (domain === "ticks") return { url: WS_BASE + "/ws/ticks", label: "/ws/ticks" };
  if (domain === "surface" || domain === "pca" || domain === "termStructure") return { url: WS_BASE + "/ws/vol", label: "/ws/vol" };
  if (domain === "risk" || domain === "portfolio" || domain === "trade") return { url: WS_BASE + "/ws/risk", label: "/ws/risk" };
  return null;
}

// Live WS message tap for one channel (lean port of the WS-monitor). Mounted
// keyed by url so switching panels reconnects cleanly.
function WsLogView({ url }: { url: string }): JSX.Element {
  const { status, count, messages, rate, paused, pause, resume, clear } = useWsLog(url, 200);
  const dot = status === "open" ? GREEN : status === "connecting" ? AMBER : RED;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div className="pp-mono" style={{ flex: "none", display: "flex", alignItems: "center", gap: 10, padding: "5px 12px", borderBottom: "1px solid #14171d", fontSize: 10.5, color: "#7b8494" }}>
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: dot }} />
        <span style={{ color: dot }}>{status}</span>
        <span><b style={{ color: "#7fa8e0" }}>{rate.toFixed(1)}</b>/s · {count} msg</span>
        <span style={{ flex: 1 }} />
        {paused ? <button onClick={resume} style={inspectBtn}>▶ resume</button> : <button onClick={pause} style={inspectBtn}>⏸ pause</button>}
        <button onClick={clear} style={inspectBtn}>clear</button>
      </div>
      <div className="pp-mono" style={{ flex: 1, overflow: "auto", minHeight: 0, padding: "6px 12px", fontSize: 11, color: "#bcd0c6", background: "#08090c" }}>
        {messages.length === 0 ? <span style={{ color: "#4d5360" }}>(no messages yet)</span> : messages.map((m, i) => (
          <div key={`${m.ts}-${i}`} style={{ marginBottom: 5 }}>
            <span style={{ color: "#4d5360" }}>{m.ts.slice(11, 19)} </span>
            <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{m.raw}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const inspectBtn: React.CSSProperties = { padding: "1px 8px", fontSize: 10, borderRadius: 3, border: "1px solid #2a3040", background: "#141a22", color: "#8fb0d8", cursor: "pointer", fontFamily: "inherit" };

// Bottom drawer: the live data the selected panel actually receives — its
// domain payload as JSON, and (when WS-fed) the raw channel message log.
function DataInspector({ pipe, fresh }: {
  pipe: PanelPipe; fresh: { data: unknown; status: string; asOf: number | null };
}): JSX.Element {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<"json" | "ws">("json");
  const chan = wsChannelFor(pipe.domain);
  const view = tab === "ws" && chan ? "ws" : "json";
  const json = useMemo(() => {
    try { return JSON.stringify(fresh.data, null, 2); } catch { return String(fresh.data); }
  }, [fresh.data]);
  const sc = fresh.status === "live" ? "#7fcf9a" : fresh.status === "stale" ? "#d9b86a" : "#e08a84";
  const tabStyle = (on: boolean): React.CSSProperties => ({ padding: "3px 11px", fontSize: 11, fontWeight: 600, borderRadius: 5, cursor: "pointer", border: "1px solid " + (on ? "#2f5c3f" : "#23272f"), background: on ? "rgba(62,196,109,.12)" : "transparent", color: on ? "#cdebd6" : "#8a909c" });
  return (
    <div style={{ flex: "none", height: open ? 246 : 34, display: "flex", flexDirection: "column", overflow: "hidden", borderTop: "1px solid #1b1e25", background: "#0b0d11", transition: "height .15s" }}>
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 8, padding: "5px 12px", background: "#0f1115" }}>
        <span className="pp-mono" style={{ fontSize: 9, letterSpacing: ".16em", color: "#5a606e" }}>DATA</span>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: "#e6eaf1" }}>{pipe.panel}</span>
        <span style={{ width: 8 }} />
        <button onClick={() => setTab("json")} style={tabStyle(view === "json")}>JSON payload</button>
        {chan ? <button onClick={() => setTab("ws")} style={tabStyle(view === "ws")}>WS {chan.label}</button> : null}
        <span style={{ flex: 1 }} />
        {view === "json" ? (
          <span className="pp-mono" style={{ fontSize: 10.5, color: sc }}>{fresh.status}{fresh.asOf ? " · " + clk(new Date(fresh.asOf)) : ""}</span>
        ) : null}
        <button onClick={() => setOpen((o) => !o)} className="pp-mono" style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, border: "1px solid #23272f", background: "transparent", color: "#8a909c", cursor: "pointer" }}>{open ? "▾ hide" : "▸ data"}</button>
      </div>
      {open ? (
        view === "ws" && chan ? (
          <WsLogView key={chan.url} url={chan.url} />
        ) : (
          <pre className="pp-mono" style={{ flex: 1, margin: 0, overflow: "auto", minHeight: 0, padding: "8px 12px", fontSize: 11, lineHeight: 1.5, color: fresh.data == null ? "#4d5360" : "#bcd0c6", background: "#08090c", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {fresh.data == null ? "(no data — " + fresh.status + ")" : json}
          </pre>
        )
      ) : null}
    </div>
  );
}

// The floating, pannable + zoomable schema canvas (grab to pan, wheel to zoom,
// double-click to reset) — same interaction model as the DB-schema tab.
function Stage({ pipe, statuses, resolveNode, asOf, domainFresh }: { pipe: PanelPipe; statuses: H[]; resolveNode: (n: PipeNode) => H; asOf: number | null; domainFresh: { data: unknown; status: string; asOf: number | null } }): JSX.Element {
  const [hoverNode, setHoverNode] = useState<number | null>(null);
  const [hoverPipe, setHoverPipe] = useState<number | null>(null);
  const DEFAULT_SCALE = 0.78;
  const [tx, setTx] = useState(40);
  const [ty, setTy] = useState(40);
  const [scale, setScale] = useState(DEFAULT_SCALE);
  const [grabbing, setGrabbing] = useState(false);
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // Center the schema in the canvas (natural content size × scale), centered
  // on both axes. Called on mount, on panel switch, and on double-click reset.
  const recenter = (s: number): void => {
    const cv = canvasRef.current;
    const ct = contentRef.current;
    if (!cv || !ct) return;
    setTx((cv.clientWidth - ct.offsetWidth * s) / 2);
    setTy((cv.clientHeight - ct.offsetHeight * s) / 2);
  };
  useLayoutEffect(() => { recenter(scale); }, [pipe.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const reset = (): void => { setScale(DEFAULT_SCALE); recenter(DEFAULT_SCALE); };
  const onMouseDown = (e: React.MouseEvent): void => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, ox: tx, oy: ty };
    setGrabbing(true);
  };
  const onMouseMove = (e: React.MouseEvent): void => {
    const d = dragRef.current;
    if (!d) return;
    setTx(d.ox + (e.clientX - d.sx));
    setTy(d.oy + (e.clientY - d.sy));
  };
  const onMouseUp = (): void => { dragRef.current = null; setGrabbing(false); };
  const onWheel = (e: React.WheelEvent): void => {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const cx = (px - tx) / scale;
    const cy = (py - ty) / scale;
    const next = Math.max(0.3, Math.min(1.6, scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
    setScale(next);
    setTx(px - cx * next);
    setTy(py - cy * next);
  };

  // `statuses` is computed over the rendered nodes (DAG when present), so the
  // stamp/footer count what's actually drawn — not the flat `pipe.nodes`, whose
  // health-less entries all collapse to the domain status (the "7 blocks down"
  // bug when only the panel's domain was missing).
  const sNodes: { label: string }[] = pipe.dag ? pipe.dag.nodes : pipe.nodes;
  const infra = pipe.nodes.slice(0, -1);
  const pstate = (a: H, b: H): "flow" | "warn" | "down" =>
    a === "down" || b === "down" ? "down" : a === "up" && b === "up" ? "flow" : "warn";
  const healthy = statuses.every((s) => s === "up");
  const downNames = sNodes.filter((_, i) => statuses[i] === "down").map((n) => n.label);
  const warnNames = sNodes.filter((_, i) => statuses[i] === "warn").map((n) => n.label);
  const stampColor = healthy ? "#7fcf9a" : downNames.length ? "#e08a84" : "#d9b86a";
  const stampText = healthy
    ? `⚡ live · updated ${asOf ? clk(new Date(asOf)) : "—"}`
    : downNames.length
      ? `⚠ ${downNames.length === 1 ? downNames[0] + " down" : downNames.length + " blocks down"}`
      : `◐ degraded · ${warnNames.join(", ")}`;

  return (
    <div className="pp-main">
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 12, padding: "11px 18px", background: "#0f1115", borderBottom: "1px solid #1b1e25" }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: "#eef1f6" }}>{pipe.panel}</span>
        <span className="pp-mono" style={{ fontSize: 9, letterSpacing: ".14em", color: "#5a606e" }}>{pipe.isolated || pipe.id === "ticker" ? "PANEL" : "VIEW"} · {VIEW_LABEL[pipe.view]}</span>
        <span style={{ flex: 1 }} />
        <span className="pp-mono" style={{ fontSize: 11, color: stampColor }}>{stampText}</span>
      </div>

      <div
        ref={canvasRef}
        className={"pp-canvas" + (grabbing ? " grabbing" : "")}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onWheel={onWheel}
        onDoubleClick={reset}
      >
        <div className="pp-stagewrap" style={{ transform: `translate(${Math.round(tx)}px, ${Math.round(ty)}px) scale(${scale})` }}>
          <div ref={contentRef} style={pipe.dag ? undefined : { display: "flex", alignItems: "stretch" }}>
            {pipe.dag ? (
              <DagSchema dag={pipe.dag} pipe={pipe} resolveNode={resolveNode} />
            ) : (
              <>
                {infra.map((n, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center" }}>
                    <Block node={n} status={statuses[i]!} tip={hoverNode === i} onEnter={() => setHoverNode(i)} onLeave={() => setHoverNode(null)} />
                    <Pipe label={pipe.edges[i] ?? ""} state={pstate(statuses[i]!, statuses[i + 1]!)} hover={hoverPipe === i} onEnter={() => setHoverPipe(i)} onLeave={() => setHoverPipe(null)} />
                  </div>
                ))}
                {/* the live panel keeps its own scroll/clicks — don't let a drag here pan the canvas */}
                <div style={{ display: "flex" }} onMouseDown={(e) => e.stopPropagation()}>
                  <Terminal pipe={pipe} live={statuses[statuses.length - 1] === "up"} />
                </div>
              </>
            )}
          </div>
        </div>

        <div className="pp-mono" style={{ position: "absolute", left: 12, bottom: 10, fontSize: 9.5, color: "#3f454c", pointerEvents: "none" }}>
          drag to pan · wheel to zoom · double-click to reset · {Math.round(scale * 100)}%
        </div>
      </div>

      <DataInspector pipe={pipe} fresh={domainFresh} />

      <div className="pp-mono" style={{ flex: "none", height: 30, display: "flex", alignItems: "center", gap: 18, padding: "0 16px", background: "#0c0d10", borderTop: "1px solid #1b1e25", fontSize: 10, color: "#4d5360" }}>
        <span style={{ color: "#5a606e" }}>domain: {pipe.domain}</span>
        <span style={{ color: "#3a3f49" }}>·</span>
        <span>{pipe.view}</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: stampColor }}>{healthy ? "◍ healthy" : "pipeline degraded"}</span>
      </div>
    </div>
  );
}

function PipelineLive(): JSX.Element {
  const [id, setId] = useState<string>(PIPELINES[0]?.id ?? "");
  const desk = useDeskData();
  const hmap = buildHealthMap(desk.system.data);
  const ws = normStatus(desk.ticks.status);
  // api is up the moment it responds (system probe came back, or ticks flow) —
  // independent of the stack's global DEGRADED flag.
  const apiUp: H = desk.system.status !== "missing" || ws === "up" ? "up" : "down";
  const resolveFor = (p: PanelPipe, n: PipeNode): H => resolve(n, hmap, normStatus(desk[p.domain].status), ws, apiUp);
  // Resolve over the rendered topology (DAG when present) so the sidebar pill
  // and the header stamp count the same blocks the schema draws.
  const statusesOf = (p: PanelPipe): H[] => (p.dag ? p.dag.nodes : p.nodes).map((n) => resolveFor(p, n));

  const healthById: Record<string, H> = {};
  for (const p of PIPELINES) healthById[p.id] = rollUp(statusesOf(p));

  const pipe = PIPELINES.find((p) => p.id === id) ?? PIPELINES[0]!;
  const statuses = statusesOf(pipe);
  const asOf = desk[pipe.domain].asOf ?? desk.ticks.asOf;

  return (
    <div className="pp-root">
      <style>{CSS}</style>
      <Sidebar id={id} setId={setId} healthById={healthById} />
      <Stage pipe={pipe} statuses={statuses} resolveNode={(n) => resolveFor(pipe, n)} asOf={asOf} domainFresh={desk[pipe.domain]} />
    </div>
  );
}

export function PipelineViz(): JSX.Element {
  return <DataProvider><PipelineLive /></DataProvider>;
}
