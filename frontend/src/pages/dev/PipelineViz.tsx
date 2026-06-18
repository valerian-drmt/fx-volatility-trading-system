/**
 * Dev "Pipeline" tab — end-to-end plumbing schematic per PROD panel.
 * Faithful to the Claude Design mockup (Pipeline Viz - Ticker bid/ask), wired
 * to real data: the panel's data domain freshness drives the flowing "current"
 * + the terminal panel's live/stale state; the Ticker terminal shows the live
 * bid/ask/mid from the ticks domain. Live is gated behind a Connect toggle
 * (/dev stays provider-free until asked).
 */
import { type CSSProperties, useState } from "react";
import { DataProvider } from "../../voldesk/data/provider";
import { useDeskData } from "../../voldesk/data/deskData";
import type { Fresh } from "../../voldesk/data/freshness";
import { PIPELINES, type NodeKind, type PanelPipe, type ViewId } from "./pipelines";

const GREEN = "#3ec46d", AMBER = "#d9a441";
const KIND: Record<NodeKind, { color: string; glyph: string; tag: string }> = {
  external: { color: "#c79a4b", glyph: "⇅", tag: "EXTERNAL" },
  container: { color: "#5b8fd6", glyph: "▣", tag: "CONTAINER" },
  store: { color: "#9b7ad6", glyph: "▤", tag: "STORE" },
  api: { color: "#46b39a", glyph: "◆", tag: "API" },
  frontend: { color: "#4d9460", glyph: "⧉", tag: "FRONT" },
  panel: { color: GREEN, glyph: "■", tag: "PANEL" },
};
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

type Stat = "live" | "stale" | "missing" | "offline";

function Block({ node, live, tip, onEnter, onLeave }: {
  node: PanelPipe["nodes"][number]; live: boolean; tip: boolean;
  onEnter: () => void; onLeave: () => void;
}): JSX.Element {
  const k = KIND[node.kind];
  const sc = live ? GREEN : node.kind === "panel" ? AMBER : "#5a606e";
  const card: CSSProperties = {
    position: "relative", background: "#181b22", border: `1px solid ${hexa(sc, live ? 0.32 : 0.4)}`,
    borderRadius: 6, padding: "11px 11px 12px", minHeight: 92, display: "flex", flexDirection: "column",
    gap: 8, overflow: "hidden", transition: "border-color .3s, box-shadow .3s",
  };
  return (
    <div style={{ flex: "none", width: 140, position: "relative" }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <div style={card}>
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: hexa(sc, 0.55) }} />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="pp-mono" style={{ width: 22, height: 22, borderRadius: 5, display: "grid", placeItems: "center", fontSize: 12, color: k.color, border: `1px solid ${hexa(k.color, 0.42)}`, background: hexa(k.color, 0.1) }}>{k.glyph}</div>
          <div className="pp-mono" style={{ fontSize: 8.5, letterSpacing: ".12em", color: k.color, fontWeight: 600 }}>{k.tag}</div>
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#e6eaf1" }}>{node.label}</div>
        <div className="pp-mono" style={{ fontSize: 9.5, color: "#6b7180", lineHeight: 1.4 }}>{node.sub}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: sc, boxShadow: `0 0 7px ${hexa(sc, 0.8)}`, animation: live ? "pppulse 1.6s ease-in-out infinite" : "none" }} />
          <span className="pp-mono" style={{ fontSize: 9, letterSpacing: ".12em", fontWeight: 600, color: sc }}>{live ? "UP" : "IDLE"}</span>
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

function Pipe({ label, live, hover, onEnter, onLeave }: {
  label: string; live: boolean; hover: boolean; onEnter: () => void; onLeave: () => void;
}): JSX.Element {
  const sc = live ? GREEN : "#5a606e";
  return (
    <div style={{ position: "relative", flex: 1, minWidth: 56, alignSelf: "stretch", display: "flex", alignItems: "center", padding: "0 1px" }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <div className="pp-mono" style={{ position: "absolute", top: "calc(50% - 19px)", left: "50%", transform: "translateX(-50%)", whiteSpace: "nowrap", fontSize: 9, letterSpacing: ".07em", textTransform: "uppercase", pointerEvents: "none", color: hover ? "#dfe7e2" : "#586070", textShadow: hover ? `0 0 9px ${hexa(sc, 0.55)}` : "none", fontWeight: hover ? 600 : 400 }}>{label}</div>
      <div style={{ position: "relative", flex: 1, height: 8, minWidth: 30, background: "#0c0e12", border: `1px solid ${hexa(sc, 0.42)}`, borderRadius: 5, overflow: "hidden", boxShadow: live ? `0 0 10px ${hexa(sc, 0.16)}, inset 0 0 6px ${hexa(sc, 0.1)}` : `inset 0 0 6px ${hexa(sc, 0.06)}`, transition: "box-shadow .35s, border-color .35s" }}>
        <div style={{ position: "absolute", inset: 0, backgroundImage: `repeating-linear-gradient(90deg, ${sc} 0, ${sc} 6px, ${hexa(sc, 0)} 6px, ${hexa(sc, 0)} 16px)`, backgroundSize: "16px 100%", animation: live ? "ppflow .6s linear infinite" : "none", opacity: live ? 0.92 : 0.4, transition: "opacity .35s" }} />
      </div>
      <div style={{ position: "absolute", right: -2, top: "50%", transform: "translateY(-50%)", lineHeight: 0 }}>
        <svg width="9" height="11" viewBox="0 0 9 11" fill="none"><path d="M1.5 1.5L6 5.5L1.5 9.5" stroke={sc} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </div>
    </div>
  );
}

function Terminal({ pipe, live, ticks }: { pipe: PanelPipe; live: boolean; ticks: Fresh<unknown> }): JSX.Element {
  const accent = live ? GREEN : AMBER;
  const card: CSSProperties = {
    width: 200, background: live ? "linear-gradient(180deg,#161b21,#13171c)" : "linear-gradient(180deg,#1a1813,#141210)",
    border: `1px solid ${hexa(accent, live ? 0.55 : 0.5)}`, borderRadius: 7, padding: "12px 13px 13px",
    display: "flex", flexDirection: "column", gap: 9, position: "relative",
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
  if (pipe.id !== "ticker") {
    return (
      <div style={{ flex: "none" }}>
        <div style={card}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: "#eef1f6" }}>{pipe.panel}</span>
            <span className="pp-mono" style={{ fontSize: 8.5, letterSpacing: ".14em", color: accent, fontWeight: 600 }}>PANEL</span>
          </div>
          {dotRow}
          <div style={{ height: 1, background: "#23272f" }} />
          <div className="pp-mono" style={{ fontSize: 11, color: live ? "#cdd3dd" : "#766f5e", lineHeight: 1.5 }}>
            {live ? "rendering live data" : "awaiting feed…"}
          </div>
        </div>
      </div>
    );
  }
  const t = ticks.data as { bid?: number | null; ask?: number | null; mid?: number | null } | null;
  const bid = t?.bid ?? null, ask = t?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : (t?.mid ?? null);
  const spread = bid != null && ask != null ? ((ask - bid) * 10000).toFixed(1) : "—";
  const val = live ? "#dfe4ec" : "#766f5e";
  return (
    <div style={{ flex: "none" }}>
      <div style={card}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: "#eef1f6" }}>Ticker bid/ask</span>
          <span className="pp-mono" style={{ fontSize: 8.5, letterSpacing: ".14em", color: accent, fontWeight: 600 }}>PANEL</span>
        </div>
        {dotRow}
        <div style={{ height: 1, background: "#23272f" }} />
        <div style={{ display: "flex", gap: 14 }}>
          {([["BID", bid], ["ASK", ask]] as const).map(([lbl, v]) => (
            <div key={lbl} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              <span className="pp-mono" style={{ fontSize: 8.5, letterSpacing: ".12em", color: "#6b7180" }}>{lbl}</span>
              <span className="pp-mono" style={{ fontSize: 15, fontWeight: 600, color: val }}>{v != null ? v.toFixed(4) : "—"}</span>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span className="pp-mono" style={{ fontSize: 8.5, letterSpacing: ".12em", color: "#6b7180" }}>MID</span>
          <span className="pp-mono" style={{ fontSize: 22, fontWeight: 700, color: live ? GREEN : "#766f5e", lineHeight: 1 }}>{mid != null ? mid.toFixed(4) : "—"}</span>
        </div>
        <div className="pp-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "#5a606e" }}>
          <span>spread {spread} pip</span>
          <span>{live ? "EUR/USD" : "no fresh ticks"}</span>
        </div>
      </div>
    </div>
  );
}

function Stage({ pipe, id, setId, connected, onToggle, freshness, ticks }: {
  pipe: PanelPipe; id: string; setId: (v: string) => void; connected: boolean; onToggle: () => void;
  freshness: { status: Stat; asOf: number | null }; ticks: Fresh<unknown>;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [hoverNode, setHoverNode] = useState<number | null>(null);
  const [hoverPipe, setHoverPipe] = useState<number | null>(null);
  const live = connected && freshness.status === "live";
  const infra = pipe.nodes.slice(0, -1);

  const byView = new Map<ViewId, PanelPipe[]>();
  for (const p of PIPELINES) byView.set(p.view, [...(byView.get(p.view) ?? []), p]);

  const stampColor = live ? "#7fcf9a" : freshness.status === "offline" ? "#6b7180" : "#d9b86a";
  const stampText = live
    ? `⚡ live · updated ${freshness.asOf ? clk(new Date(freshness.asOf)) : "—"}`
    : freshness.status === "offline" ? "○ offline — connect to feed the pipes"
    : freshness.status === "missing" ? "⚠ no data yet" : "⚠ stale";

  return (
    <div className="pp-root">
      <style>{CSS}</style>
      {/* toolbar */}
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
        <button onClick={onToggle} className="pp-mono" style={{ fontSize: 11, padding: "6px 12px", borderRadius: 6, border: "none", cursor: "pointer", background: connected ? "#2a2f38" : "#1f6f63", color: "#fff" }}>
          {connected ? "⏸ disconnect" : "⚡ connect"}
        </button>
      </div>

      {/* schema */}
      <div className="pp-area" style={{ flex: 1, overflow: "auto", display: "flex", alignItems: "center", padding: "44px 34px", background: "radial-gradient(ellipse 80% 120% at 50% 0%, #121620 0%, #0e0e0e 60%)" }}>
        <div style={{ display: "flex", alignItems: "center", margin: "auto" }}>
          {infra.map((n, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center" }}>
              <Block node={n} live={live} tip={hoverNode === i} onEnter={() => setHoverNode(i)} onLeave={() => setHoverNode(null)} />
              <Pipe label={pipe.edges[i] ?? ""} live={live} hover={hoverPipe === i} onEnter={() => setHoverPipe(i)} onLeave={() => setHoverPipe(null)} />
            </div>
          ))}
          <Terminal pipe={pipe} live={live} ticks={ticks} />
        </div>
      </div>

      {/* status bar */}
      <div className="pp-mono" style={{ flex: "none", height: 30, display: "flex", alignItems: "center", gap: 18, padding: "0 16px", background: "#0c0d10", borderTop: "1px solid #1b1e25", fontSize: 10, color: "#4d5360" }}>
        <span style={{ color: "#5a606e" }}>domain: {pipe.domain}</span>
        <span style={{ color: "#3a3f49" }}>·</span>
        <span>{pipe.view}</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: stampColor }}>{live ? "◍ healthy" : connected ? "pipeline idle" : "disconnected"}</span>
      </div>
    </div>
  );
}

const EMPTY_TICK: Fresh<unknown> = { data: null, status: "missing", asOf: null, ageMs: null };

function LiveStage(props: { pipe: PanelPipe; id: string; setId: (v: string) => void; onToggle: () => void }): JSX.Element {
  const desk = useDeskData();
  const f = desk[props.pipe.domain];
  return <Stage {...props} connected freshness={{ status: f.status, asOf: f.asOf }} ticks={desk.ticks} />;
}

export function PipelineViz(): JSX.Element {
  const [id, setId] = useState<string>(PIPELINES[0]?.id ?? "");
  const [connected, setConnected] = useState(false);
  const pipe = PIPELINES.find((p) => p.id === id) ?? PIPELINES[0]!;
  const toggle = (): void => setConnected((c) => !c);
  return connected ? (
    <DataProvider><LiveStage pipe={pipe} id={id} setId={setId} onToggle={toggle} /></DataProvider>
  ) : (
    <Stage pipe={pipe} id={id} setId={setId} connected={false} onToggle={toggle} freshness={{ status: "offline", asOf: null }} ticks={EMPTY_TICK} />
  );
}
