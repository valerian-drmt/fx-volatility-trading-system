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
import { type CSSProperties, useState } from "react";
import { DataProvider } from "../../voldesk/data/provider";
import { type SystemData, useDeskData } from "../../voldesk/data/deskData";
import type { Fresh } from "../../voldesk/data/freshness";
import { PIPELINES, type NodeKind, type PanelPipe, type PipeNode, type ViewId } from "./pipelines";

const GREEN = "#3ec46d", AMBER = "#d9a441", RED = "#e0564f";
type H = "up" | "warn" | "down";
const HCOLOR: Record<H, string> = { up: GREEN, warn: AMBER, down: RED };

// Two block categories → two colours only: the external broker vs everything
// we run (containers + stores + api + front all read as "container").
const BLOCK_STYLE = {
  external: { color: "#c79a4b", glyph: "⇅", tag: "EXTERNAL" },
  container: { color: "#5b8fd6", glyph: "▣", tag: "CONTAINER" },
} as const;
const blockStyle = (kind: NodeKind): { color: string; glyph: string; tag: string } =>
  kind === "external" ? BLOCK_STYLE.external : BLOCK_STYLE.container;
const VIEW_LABEL: Record<ViewId, string> = {
  dashboard: "Dashboard", trade: "Trade", signals: "Signal", risk: "Risk", portfolio: "Portfolio",
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
function resolve(node: PipeNode, hmap: Record<string, H>, domain: H, ws: H): H {
  if (node.health === "__self") return "up";
  if (node.health === "__ws") return ws;
  if (node.health && hmap[node.health]) return hmap[node.health]!;
  return domain; // fallback: panel's domain freshness
}

const CSS = `
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
@keyframes ppflow { from{background-position:0 0} to{background-position:16px 0} }
@keyframes pppulse { 0%,100%{opacity:1} 50%{opacity:.4} }
@keyframes pphalo {
  0%,100%{box-shadow:0 0 0 1px rgba(62,196,109,.22),0 0 22px rgba(62,196,109,.16),inset 0 0 30px rgba(62,196,109,.05)}
  50%{box-shadow:0 0 0 1px rgba(62,196,109,.32),0 0 32px rgba(62,196,109,.28),inset 0 0 34px rgba(62,196,109,.08)}
}
.pp-root{height:calc(100vh - 92px);display:flex;flex-direction:column;overflow:hidden;background:#0e0e0e;color:#d4d8e0;font-family:'IBM Plex Sans',system-ui,sans-serif}
.pp-root *{box-sizing:border-box}
.pp-mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
.pp-area::-webkit-scrollbar{height:9px;width:9px}
.pp-area::-webkit-scrollbar-thumb{background:#262a33;border-radius:5px}
`;

function XMark({ size }: { size: number }): JSX.Element {
  return <svg width={size} height={size} viewBox="0 0 28 28" fill="none"><path d="M7 7L21 21M21 7L7 21" stroke={RED} strokeWidth="3" strokeLinecap="round" /></svg>;
}

function Block({ node, status, tip, onEnter, onLeave }: {
  node: PipeNode; status: H; tip: boolean; onEnter: () => void; onLeave: () => void;
}): JSX.Element {
  const k = blockStyle(node.kind);
  const sc = HCOLOR[status];
  const down = status === "down";
  return (
    <div style={{ flex: "none", width: 188, position: "relative" }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      {down ? <div style={{ position: "absolute", left: "50%", top: -42, transform: "translateX(-50%)", lineHeight: 0, zIndex: 6 }}><XMark size={34} /></div> : null}
      <div style={{ position: "relative", background: down ? "#1b1517" : "#181b22", border: `1px solid ${hexa(sc, down ? 0.5 : 0.32)}`, borderRadius: 7, padding: "15px 15px 16px", minHeight: 122, display: "flex", flexDirection: "column", gap: 11, overflow: "hidden", boxShadow: down ? `0 0 16px ${hexa(RED, 0.14)}, inset 0 0 22px ${hexa(RED, 0.05)}` : "none", transition: "background .25s, border-color .25s, box-shadow .25s" }}>
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: hexa(sc, down ? 0.7 : 0.55) }} />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="pp-mono" style={{ width: 30, height: 30, borderRadius: 6, display: "grid", placeItems: "center", fontSize: 17, color: k.color, border: `1px solid ${hexa(k.color, 0.42)}`, background: hexa(k.color, 0.1), opacity: down ? 0.6 : 1 }}>{k.glyph}</div>
          <div className="pp-mono" style={{ fontSize: 10.5, letterSpacing: ".12em", color: k.color, fontWeight: 600, opacity: down ? 0.7 : 1 }}>{k.tag}</div>
        </div>
        <div style={{ fontSize: 16.5, fontWeight: 600, color: "#e6eaf1" }}>{node.label}</div>
        <div className="pp-mono" style={{ fontSize: 12, color: "#6b7180", lineHeight: 1.45 }}>{node.sub}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 9, height: 9, borderRadius: "50%", background: sc, boxShadow: `0 0 8px ${hexa(sc, 0.8)}`, animation: status === "up" ? "pppulse 1.6s ease-in-out infinite" : "none" }} />
          <span className="pp-mono" style={{ fontSize: 11.5, letterSpacing: ".12em", fontWeight: 600, color: sc }}>{status === "up" ? "UP" : status === "warn" ? "WARN" : "DOWN"}</span>
        </div>
      </div>
      {tip ? (
        <div className="pp-mono" style={{ position: "absolute", top: "calc(100% + 9px)", left: "50%", transform: "translateX(-50%)", whiteSpace: "nowrap", background: "#20242d", border: "1px solid #313742", borderRadius: 6, padding: "6px 9px", fontSize: 10, color: "#c3c8d2", boxShadow: "0 8px 22px rgba(0,0,0,.5)", zIndex: 15 }}>
          {node.kind}: {node.label} — {node.sub}
        </div>
      ) : null}
    </div>
  );
}

function Pipe({ label, flow, broken, hover, onEnter, onLeave }: {
  label: string; flow: boolean; broken: boolean; hover: boolean; onEnter: () => void; onLeave: () => void;
}): JSX.Element {
  const sc = flow ? GREEN : broken ? RED : "#5a606e";
  return (
    <div style={{ position: "relative", flex: 1, minWidth: 156, alignSelf: "stretch", display: "flex", alignItems: "center", padding: "0 1px" }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <div className="pp-mono" style={{ position: "absolute", top: "calc(50% - 27px)", left: "50%", transform: "translateX(-50%)", whiteSpace: "nowrap", fontSize: 11.5, letterSpacing: ".07em", textTransform: "uppercase", pointerEvents: "none", color: hover ? "#dfe7e2" : "#7b8494", textShadow: hover ? `0 0 9px ${hexa(sc, 0.55)}` : "none", fontWeight: hover ? 600 : 500 }}>{label}</div>
      <div style={{ position: "relative", flex: 1, height: 10, minWidth: 30, background: "#0c0e12", border: `1px solid ${hexa(sc, 0.42)}`, borderRadius: 6, overflow: "hidden", boxShadow: flow ? `0 0 10px ${hexa(sc, 0.16)}, inset 0 0 6px ${hexa(sc, 0.1)}` : `inset 0 0 6px ${hexa(sc, 0.06)}`, transition: "box-shadow .35s, border-color .35s" }}>
        <div style={{ position: "absolute", inset: 0, backgroundImage: `repeating-linear-gradient(90deg, ${sc} 0, ${sc} 6px, ${hexa(sc, 0)} 6px, ${hexa(sc, 0)} 16px)`, backgroundSize: "16px 100%", animation: flow ? "ppflow .6s linear infinite" : "none", opacity: flow ? 0.92 : 0.45, transition: "opacity .35s" }} />
      </div>
      <div style={{ position: "absolute", right: -3, top: "50%", transform: "translateY(-50%)", lineHeight: 0 }}>
        <svg width="12" height="15" viewBox="0 0 9 11" fill="none"><path d="M1.5 1.5L6 5.5L1.5 9.5" stroke={sc} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </div>
    </div>
  );
}

function Terminal({ pipe, live, ticks }: { pipe: PanelPipe; live: boolean; ticks: Fresh<unknown> }): JSX.Element {
  const accent = live ? GREEN : AMBER;
  const card: CSSProperties = {
    width: 234, background: live ? "linear-gradient(180deg,#161b21,#13171c)" : "linear-gradient(180deg,#1a1813,#141210)",
    border: `1px solid ${hexa(accent, live ? 0.55 : 0.5)}`, borderRadius: 8, padding: "14px 15px 15px",
    display: "flex", flexDirection: "column", gap: 11, position: "relative",
    boxShadow: live ? "0 0 0 1px rgba(62,196,109,.25),0 0 26px rgba(62,196,109,.2),inset 0 0 30px rgba(62,196,109,.05)" : "inset 0 0 26px rgba(217,164,65,.06)",
    animation: live ? "pphalo 2.6s ease-in-out infinite" : "none", transition: "border-color .4s, box-shadow .4s, background .4s",
  };
  const dotRow = (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: accent, boxShadow: `0 0 7px ${hexa(accent, 0.85)}`, animation: live ? "pppulse 1.4s ease-in-out infinite" : "none" }} />
      <span className="pp-mono" style={{ fontSize: 9.5, letterSpacing: ".04em", color: live ? "#7fcf9a" : "#d9b86a" }}>{live ? "live" : "stale"}</span>
      <span style={{ flex: 1 }} />
      <span className="pp-mono" style={{ fontSize: 9, color: "#5a606e" }}>{pipe.domain}</span>
    </div>
  );
  const head = (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <span style={{ fontSize: 14.5, fontWeight: 600, color: "#eef1f6" }}>{pipe.id === "ticker" ? "Ticker bid/ask" : pipe.panel}</span>
      <span className="pp-mono" style={{ fontSize: 10, letterSpacing: ".14em", color: accent, fontWeight: 600 }}>PANEL</span>
    </div>
  );
  if (pipe.id !== "ticker") {
    return (
      <div style={{ flex: "none" }}><div style={card}>{head}{dotRow}<div style={{ height: 1, background: "#23272f" }} />
        <div className="pp-mono" style={{ fontSize: 11, color: live ? "#cdd3dd" : "#766f5e", lineHeight: 1.5 }}>{live ? "rendering live data" : "awaiting feed…"}</div>
      </div></div>
    );
  }
  const t = ticks.data as { bid?: number | null; ask?: number | null; mid?: number | null } | null;
  const bid = t?.bid ?? null, ask = t?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : (t?.mid ?? null);
  const spread = bid != null && ask != null ? ((ask - bid) * 10000).toFixed(1) : "—";
  const val = live ? "#dfe4ec" : "#766f5e";
  return (
    <div style={{ flex: "none" }}><div style={card}>{head}{dotRow}<div style={{ height: 1, background: "#23272f" }} />
      <div style={{ display: "flex", gap: 14 }}>
        {([["BID", bid], ["ASK", ask]] as const).map(([lbl, v]) => (
          <div key={lbl} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span className="pp-mono" style={{ fontSize: 10, letterSpacing: ".12em", color: "#6b7180" }}>{lbl}</span>
            <span className="pp-mono" style={{ fontSize: 18, fontWeight: 600, color: val }}>{v != null ? v.toFixed(4) : "—"}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="pp-mono" style={{ fontSize: 10, letterSpacing: ".12em", color: "#6b7180" }}>MID</span>
        <span className="pp-mono" style={{ fontSize: 27, fontWeight: 700, color: live ? GREEN : "#766f5e", lineHeight: 1 }}>{mid != null ? mid.toFixed(4) : "—"}</span>
      </div>
      <div className="pp-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "#5a606e" }}>
        <span>spread {spread} pip</span><span>{live ? "EUR/USD" : "no fresh ticks"}</span>
      </div>
    </div></div>
  );
}

function Stage({ pipe, id, setId, statuses, asOf, ticks }: {
  pipe: PanelPipe; id: string; setId: (v: string) => void; statuses: H[]; asOf: number | null; ticks: Fresh<unknown>;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [hoverNode, setHoverNode] = useState<number | null>(null);
  const [hoverPipe, setHoverPipe] = useState<number | null>(null);
  const infra = pipe.nodes.slice(0, -1);
  const flows = statuses.slice(0, -1).map((_, i) => statuses[i] === "up" && statuses[i + 1] === "up");
  const healthy = statuses.every((s) => s === "up");
  const downNames = pipe.nodes.filter((_, i) => statuses[i] === "down").map((n) => n.label);

  const byView = new Map<ViewId, PanelPipe[]>();
  for (const p of PIPELINES) byView.set(p.view, [...(byView.get(p.view) ?? []), p]);

  const stampColor = healthy ? "#7fcf9a" : "#d9b86a";
  const stampText = healthy
    ? `⚡ live · updated ${asOf ? clk(new Date(asOf)) : "—"}`
    : `⚠ degraded · ${downNames.length === 1 ? downNames[0] + " down" : downNames.length + " blocks down"}`;

  return (
    <div className="pp-root">
      <style>{CSS}</style>
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 14, padding: "11px 18px", background: "#0f1115", borderBottom: "1px solid #1b1e25", position: "relative", zIndex: 20 }}>
        <div style={{ position: "relative" }}>
          <button onClick={() => setOpen((o) => !o)} style={{ display: "flex", alignItems: "center", gap: 9, padding: "6px 11px", background: "#181b22", border: "1px solid #2a2f38", borderRadius: 7, cursor: "pointer", color: "#e6eaf1" }}>
            <span className="pp-mono" style={{ fontSize: 8.5, letterSpacing: ".14em", color: "#5a606e" }}>PANEL</span>
            <span style={{ fontSize: 13, fontWeight: 500 }}>{pipe.panel}</span>
            <span style={{ fontSize: 9, color: "#6b7180" }}>▼</span>
          </button>
          {open ? (
            <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, minWidth: 240, maxHeight: 420, overflow: "auto", background: "#161a20", border: "1px solid #2a2f38", borderRadius: 8, padding: 5, zIndex: 40, boxShadow: "0 12px 34px rgba(0,0,0,.55)" }}>
              {[...byView.entries()].map(([view, panels]) => (
                <div key={view}>
                  <div className="pp-mono" style={{ fontSize: 8.5, letterSpacing: ".12em", color: "#4d5360", padding: "7px 9px 3px" }}>{VIEW_LABEL[view].toUpperCase()}</div>
                  {panels.map((p) => (
                    <div key={p.id} onClick={() => { setId(p.id); setOpen(false); }} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 9px", borderRadius: 6, cursor: "pointer", background: p.id === id ? "rgba(62,196,109,.1)" : "transparent" }}>
                      <span style={{ fontSize: 12.5, color: p.id === id ? "#e6f6ec" : "#aab0bd" }}>{p.panel}</span>
                      {p.id === id ? <span style={{ color: GREEN, fontSize: 12 }}>✓</span> : null}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <span style={{ flex: 1 }} />
        <span className="pp-mono" style={{ fontSize: 11, color: stampColor }}>{stampText}</span>
      </div>

      <div className="pp-area" style={{ flex: 1, overflow: "auto", display: "flex", alignItems: "center", padding: "44px 34px", background: "radial-gradient(ellipse 80% 120% at 50% 0%, #121620 0%, #0e0e0e 60%)" }}>
        <div style={{ display: "flex", alignItems: "center", margin: "auto" }}>
          {infra.map((n, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center" }}>
              <Block node={n} status={statuses[i]!} tip={hoverNode === i} onEnter={() => setHoverNode(i)} onLeave={() => setHoverNode(null)} />
              <Pipe label={pipe.edges[i] ?? ""} flow={flows[i]!} broken={statuses[i] === "down" || statuses[i + 1] === "down"} hover={hoverPipe === i} onEnter={() => setHoverPipe(i)} onLeave={() => setHoverPipe(null)} />
            </div>
          ))}
          <Terminal pipe={pipe} live={statuses[statuses.length - 1] === "up"} ticks={ticks} />
        </div>
      </div>

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

function LiveStage({ pipe, id, setId }: { pipe: PanelPipe; id: string; setId: (v: string) => void }): JSX.Element {
  const desk = useDeskData();
  const domainF = desk[pipe.domain];
  const ticks = desk.ticks;
  const hmap = buildHealthMap(desk.system.data);
  const dom = normStatus(domainF.status);
  const ws = normStatus(ticks.status);
  const statuses = pipe.nodes.map((n) => resolve(n, hmap, dom, ws));
  return <Stage pipe={pipe} id={id} setId={setId} statuses={statuses} asOf={domainF.asOf ?? ticks.asOf} ticks={ticks} />;
}

export function PipelineViz(): JSX.Element {
  const [id, setId] = useState<string>(PIPELINES[0]?.id ?? "");
  const pipe = PIPELINES.find((p) => p.id === id) ?? PIPELINES[0]!;
  return <DataProvider><LiveStage pipe={pipe} id={id} setId={setId} /></DataProvider>;
}
