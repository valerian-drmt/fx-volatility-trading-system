/**
 * Dev "Pipeline" tab — one full end-to-end plumbing schematic per PROD panel.
 *
 * Distinguishes prod (voldesk, 5 tabs of small panels) from dev: the listbox is
 * the granular prod panels. Each renders a single left→right pipe diagram —
 * IB → ib-gateway → … → api → frontend → the panel itself (terminal block).
 * When the panel's data domain is LIVE the current flows through the pipes and
 * the "last update" stamp ticks; stale/offline → pipes dead. Live data is gated
 * behind a Connect toggle (/dev stays provider-free until asked); connecting
 * reads the domain freshness — no view is rendered, the panel is just the end
 * of the schema.
 */
import { useState } from "react";
import { DataProvider } from "../../voldesk/data/provider";
import { useDeskData } from "../../voldesk/data/deskData";
import { PIPELINES, type NodeKind, type PanelPipe, type ViewId } from "./pipelines";

const KIND_STYLE: Record<NodeKind, { bg: string; border: string }> = {
  external: { bg: "#3a2a26", border: "#8a6a52" },
  container: { bg: "#1c2a3c", border: "#3f6296" },
  store: { bg: "#27223f", border: "#6a55b0" },
  api: { bg: "#16332f", border: "#3a8f7c" },
  frontend: { bg: "#1d3a23", border: "#4d9460" },
  panel: { bg: "#123524", border: "#38e0c8" },
};

const VIEW_LABEL: Record<ViewId, string> = {
  dashboard: "Dashboard", trade: "Trade", signals: "Signal", risk: "Risk", portfolio: "Portfolio",
};

const CSS = `
.pp-wrap { display:flex; flex-direction:column; height:calc(100vh - 92px); color:#ddd; }
.pp-bar { display:flex; align-items:center; gap:12px; padding:10px 14px; border-bottom:1px solid #333; flex-wrap:wrap; }
.pp-sel { background:#1a1a1a; color:#ddd; border:1px solid #444; border-radius:4px; padding:5px 8px; font-size:13px; min-width:300px; }
.pp-btn { background:#1f6f63; color:#fff; border:none; border-radius:4px; padding:6px 14px; cursor:pointer; font-size:13px; }
.pp-body { flex:1; overflow:auto; display:flex; flex-direction:column; }
.pp-stamp { display:flex; align-items:center; gap:7px; font-family:monospace; font-size:12px; padding:14px 18px 0; }
.pp-dot { width:8px; height:8px; border-radius:50%; }
.pp-dot.on { background:#38e0c8; box-shadow:0 0 7px #38e0c8; animation:ppulse 1.4s ease-in-out infinite; }
.pp-dot.off { background:#5a6472; }
.pp-flow { display:flex; align-items:center; overflow-x:auto; padding:30px 18px; flex:1; }
.pp-node { flex:0 0 auto; width:164px; border-radius:8px; padding:10px 12px; }
.pp-node.panel { width:180px; }
.pp-node.panel.lit { box-shadow:0 0 16px -2px #38e0c8; }
.pp-node .lbl { font-weight:700; font-size:13px; color:#eee; }
.pp-node .sub { font-size:10.5px; color:#9aa; margin-top:3px; font-family:monospace; line-height:1.3; }
.pp-node .tag { font-size:9px; text-transform:uppercase; letter-spacing:.5px; float:right; opacity:.85; }
.pp-seg { flex:0 0 auto; display:flex; flex-direction:column; align-items:center; gap:5px; width:96px; }
.pp-edge { font-size:10px; color:#8a93a3; font-family:monospace; text-align:center; white-space:normal; line-height:1.2; min-height:24px; }
.pp-pipe { display:flex; align-items:center; width:100%; }
.pp-pipe .tube { flex:1; height:9px; border-radius:5px; background:#222d40; }
.pp-pipe.flow .tube { background:repeating-linear-gradient(90deg,#38e0c8 0 5px,transparent 5px 16px),#222d40; animation:ppfh .55s linear infinite; }
.pp-pipe .head { color:#3a5a6a; font-size:13px; margin-left:-3px; }
.pp-pipe.flow .head { color:#38e0c8; }
@keyframes ppfh { from{background-position:0 0,0 0} to{background-position:16px 0,0 0} }
@keyframes ppulse { 0%,100%{opacity:1} 50%{opacity:.45} }
`;

function NodeBox({ node, lit }: { node: PipeNodeT; lit: boolean }): JSX.Element {
  const s = KIND_STYLE[node.kind];
  return (
    <div className={`pp-node ${node.kind}${lit && node.kind === "panel" ? " lit" : ""}`} style={{ background: s.bg, border: `${node.kind === "panel" ? 2 : 1}px solid ${s.border}` }}>
      <div><span className="lbl">{node.label}</span><span className="tag" style={{ color: s.border }}>{node.kind}</span></div>
      {node.sub ? <div className="sub">{node.sub}</div> : null}
    </div>
  );
}

type PipeNodeT = PanelPipe["nodes"][number];

function Schema({ pipe, flow }: { pipe: PanelPipe; flow: boolean }): JSX.Element {
  return (
    <div className="pp-flow">
      {pipe.nodes.map((n, i) => (
        <div key={i} style={{ display: "contents" }}>
          <NodeBox node={n} lit={flow} />
          {i < pipe.nodes.length - 1 ? (
            <div className="pp-seg">
              <span className="pp-edge">{pipe.edges[i]}</span>
              <div className={`pp-pipe${flow ? " flow" : ""}`}>
                <span className="tube" />
                <span className="head">▶</span>
              </div>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Stamp({ status, asOf }: { status: "live" | "stale" | "missing" | "offline"; asOf: number | null }): JSX.Element {
  const on = status === "live";
  const detail =
    status === "offline" ? "not connected"
    : status === "missing" ? "no data yet"
    : asOf ? `updated ${new Date(asOf).toLocaleTimeString()}` : status;
  return (
    <div className="pp-stamp">
      <span className={`pp-dot ${on ? "on" : "off"}`} />
      <span style={{ color: on ? "#38e0c8" : "#8a93a3" }}>{on ? "⚡ live" : status === "offline" ? "○ offline" : "○ " + status}</span>
      <span style={{ color: "#778" }}>· {detail}</span>
    </div>
  );
}

// Inside DataProvider: the panel's domain freshness drives the flow + stamp.
function LiveBody({ pipe }: { pipe: PanelPipe }): JSX.Element {
  const desk = useDeskData();
  const f = desk[pipe.domain];
  return (
    <div className="pp-body">
      <Stamp status={f.status} asOf={f.asOf} />
      <Schema pipe={pipe} flow={f.status === "live"} />
    </div>
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
        <span style={{ fontSize: 13, color: "#aaa" }}>Prod panel</span>
        <select className="pp-sel" value={id} onChange={(e) => setId(e.target.value)}>
          {[...byView.entries()].map(([view, panels]) => (
            <optgroup key={view} label={VIEW_LABEL[view]}>
              {panels.map((p) => <option key={p.id} value={p.id}>{p.panel}</option>)}
            </optgroup>
          ))}
        </select>
        <button className="pp-btn" type="button" onClick={() => setConnected((c) => !c)}>
          {connected ? "⏸ Disconnect" : "⚡ Connect (live)"}
        </button>
      </div>

      {connected ? (
        <DataProvider><LiveBody pipe={pipe} /></DataProvider>
      ) : (
        <div className="pp-body">
          <Stamp status="offline" asOf={null} />
          <Schema pipe={pipe} flow={false} />
        </div>
      )}
    </div>
  );
}
