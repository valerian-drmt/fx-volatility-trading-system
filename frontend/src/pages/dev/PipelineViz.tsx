/**
 * Dev "Pipeline" tab — per-panel plumbing/network schematic.
 *
 * Each panel → a chain of container blocks joined by pipes. When the panel's
 * data domain is LIVE the pipes show a flowing "current" (animated) and the
 * "last update" stamp ticks; stale/offline → pipes are dead (static). The
 * pipeline terminates by feeding the panel's west face: the panel itself is
 * rendered live on the right (it is the frontend destination).
 *
 * `/dev` is provider-free, so the live data is gated behind a "connect" toggle —
 * the diagram shows immediately (dead pipes), connecting opens the feeds and the
 * current starts flowing = proof of live.
 */
import { type ReactNode, useState } from "react";
import { DataProvider } from "../../voldesk/data/provider";
import { useDeskData } from "../../voldesk/data/deskData";
import { DashboardView } from "../../voldesk/views/DashboardView";
import { PortfolioView } from "../../voldesk/views/PortfolioView";
import { RiskView } from "../../voldesk/views/RiskView";
import { SignalsView } from "../../voldesk/views/SignalsView";
import { SystemView } from "../../voldesk/views/SystemView";
import { TradeView } from "../../voldesk/views/TradeView";
import { PIPELINES, type NodeKind, type PanelPipe, type ViewId } from "./pipelines";

const VIEW_COMPONENTS: Record<ViewId, () => JSX.Element> = {
  signals: SignalsView,
  trade: () => <TradeView tweaks={{ density: "comfortable", showGreeks: true }} />,
  portfolio: PortfolioView,
  risk: RiskView,
  dashboard: () => <DashboardView go={() => undefined} />,
  system: SystemView,
};

const KIND_STYLE: Record<NodeKind, { bg: string; border: string }> = {
  external: { bg: "#3a2a26", border: "#8a6a52" },
  container: { bg: "#1c2a3c", border: "#3f6296" },
  store: { bg: "#27223f", border: "#6a55b0" },
  api: { bg: "#16332f", border: "#3a8f7c" },
};

const CSS = `
.pp-wrap { display:flex; flex-direction:column; height: calc(100vh - 92px); color:#ddd; }
.pp-bar { display:flex; align-items:center; gap:12px; padding:10px 14px; border-bottom:1px solid #333; flex-wrap:wrap; }
.pp-sel { background:#1a1a1a; color:#ddd; border:1px solid #444; border-radius:4px; padding:5px 8px; font-size:13px; min-width:280px; }
.pp-stage { display:flex; flex:1; min-height:0; }
.pp-diag { flex:0 0 380px; overflow:auto; padding:16px 18px; border-right:1px solid #2a2a2a; }
.pp-panel { flex:1; overflow:auto; background:#0f1115; border-left:3px solid #243044; }
.pp-panel.flow { border-left-color:#38e0c8; box-shadow: inset 14px 0 26px -18px #38e0c8; }
.pp-stamp { display:flex; align-items:center; gap:7px; font-family:monospace; font-size:12px; margin-bottom:16px; }
.pp-dot { width:8px; height:8px; border-radius:50%; }
.pp-dot.on { background:#38e0c8; box-shadow:0 0 7px #38e0c8; animation:ppulse 1.4s ease-in-out infinite; }
.pp-dot.off { background:#5a6472; }
.pp-node { width:300px; max-width:100%; border-radius:6px; padding:8px 12px; }
.pp-node .lbl { font-weight:700; font-size:13px; color:#eee; }
.pp-node .sub { font-size:11px; color:#9aa; margin-top:2px; font-family:monospace; }
.pp-node .tag { font-size:9px; text-transform:uppercase; letter-spacing:.5px; float:right; opacity:.8; }
.pp-seg { display:flex; align-items:center; gap:10px; height:34px; }
.pp-pipe { background:#222d40; border-radius:5px; }
.pp-pipe.v { width:8px; height:30px; margin-left:24px; }
.pp-pipe.h { height:8px; width:70px; }
.pp-pipe.flow.v { background:repeating-linear-gradient(180deg,#38e0c8 0 4px,transparent 4px 13px),#222d40; animation:ppfv .5s linear infinite; }
.pp-pipe.flow.h { background:repeating-linear-gradient(90deg,#38e0c8 0 4px,transparent 4px 13px),#222d40; animation:ppfh .5s linear infinite; }
.pp-edge { font-size:11px; color:#8a93a3; font-family:monospace; }
.pp-inlet { display:flex; align-items:center; gap:8px; margin-left:18px; margin-top:4px; }
.pp-inlet .arrow { color:#38e0c8; font-size:16px; }
.pp-connect { display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; gap:12px; color:#888; }
.pp-btn { background:#1f6f63; color:#fff; border:none; border-radius:4px; padding:8px 16px; cursor:pointer; font-size:13px; }
@keyframes ppfv { from{background-position:0 0,0 0} to{background-position:0 13px,0 0} }
@keyframes ppfh { from{background-position:0 0,0 0} to{background-position:13px 0,0 0} }
@keyframes ppulse { 0%,100%{opacity:1} 50%{opacity:.45} }
`;

function NodeBox({ node }: { node: PanelPipe["nodes"][number] }): JSX.Element {
  const s = KIND_STYLE[node.kind];
  return (
    <div className="pp-node" style={{ background: s.bg, border: `1px solid ${s.border}` }}>
      <div><span className="lbl">{node.label}</span><span className="tag" style={{ color: s.border }}>{node.kind}</span></div>
      {node.sub ? <div className="sub">{node.sub}</div> : null}
    </div>
  );
}

function Diagram({ pipe, flow }: { pipe: PanelPipe; flow: boolean }): JSX.Element {
  const f = flow ? " flow" : "";
  return (
    <div>
      {pipe.nodes.map((n, i) => (
        <div key={i}>
          <NodeBox node={n} />
          {i < pipe.nodes.length - 1 ? (
            <div className="pp-seg">
              <div className={`pp-pipe v${f}`} />
              <span className="pp-edge">{pipe.edges[i]}</span>
            </div>
          ) : null}
        </div>
      ))}
      {/* final pipe → panel west face */}
      <div className="pp-inlet">
        <div className={`pp-pipe h${f}`} />
        <span className="arrow">▶</span>
        <span className="pp-edge" style={{ color: "#7c9" }}>{pipe.endpoint}</span>
      </div>
    </div>
  );
}

function Stamp({ status, asOf }: { status: "live" | "stale" | "missing" | "offline"; asOf: number | null }): JSX.Element {
  const on = status === "live";
  const text =
    status === "offline" ? "not connected"
    : status === "missing" ? "no data"
    : asOf ? `updated ${new Date(asOf).toLocaleTimeString()}` : status;
  return (
    <div className="pp-stamp">
      <span className={`pp-dot ${on ? "on" : "off"}`} />
      <span style={{ color: on ? "#38e0c8" : "#8a93a3" }}>{on ? "⚡ live" : status === "offline" ? "○ offline" : "○ " + status}</span>
      <span style={{ color: "#778" }}>· {text}</span>
    </div>
  );
}

function Stage({ pipe, flow, status, asOf, children }: {
  pipe: PanelPipe; flow: boolean; status: "live" | "stale" | "missing" | "offline"; asOf: number | null; children: ReactNode;
}): JSX.Element {
  return (
    <div className="pp-stage">
      <div className="pp-diag">
        <Stamp status={status} asOf={asOf} />
        <Diagram pipe={pipe} flow={flow} />
      </div>
      <div className={`pp-panel${flow ? " flow" : ""}`}>{children}</div>
    </div>
  );
}

// Inside DataProvider: read the panel's domain freshness → drives flow + stamp.
function LiveStage({ pipe }: { pipe: PanelPipe }): JSX.Element {
  const desk = useDeskData();
  const f = desk[pipe.domain];
  const ViewComp = VIEW_COMPONENTS[pipe.view];
  return (
    <Stage pipe={pipe} flow={f.status === "live"} status={f.status} asOf={f.asOf}>
      <div key={pipe.view}><ViewComp /></div>
    </Stage>
  );
}

export function PipelineViz(): JSX.Element {
  const [id, setId] = useState<string>(PIPELINES[0]?.id ?? "");
  const [connected, setConnected] = useState(false);
  const pipe = PIPELINES.find((p) => p.id === id) ?? PIPELINES[0]!;

  const byView = new Map<ViewId, PanelPipe[]>();
  for (const p of PIPELINES) byView.set(p.view, [...(byView.get(p.view) ?? []), p]);

  return (
    <div className="pp-wrap">
      <style>{CSS}</style>
      <div className="pp-bar">
        <span style={{ fontSize: 13, color: "#aaa" }}>Panel</span>
        <select className="pp-sel" value={id} onChange={(e) => setId(e.target.value)}>
          {[...byView.entries()].map(([view, panels]) => (
            <optgroup key={view} label={view}>
              {panels.map((p) => <option key={p.id} value={p.id}>{p.panel}</option>)}
            </optgroup>
          ))}
        </select>
        <button className="pp-btn" type="button" onClick={() => setConnected((c) => !c)}>
          {connected ? "⏸ Disconnect" : "⚡ Connect (live)"}
        </button>
      </div>

      {connected ? (
        <DataProvider><LiveStage pipe={pipe} /></DataProvider>
      ) : (
        <Stage pipe={pipe} flow={false} status="offline" asOf={null}>
          <div className="pp-connect">
            <div style={{ fontSize: 13 }}>Live <b style={{ color: "#bbb" }}>{pipe.view}</b> panel — connect to feed the pipes</div>
            <button className="pp-btn" type="button" onClick={() => setConnected(true)}>⚡ Connect (live)</button>
          </div>
        </Stage>
      )}
    </div>
  );
}
