/**
 * Dev "Pipeline" tab — per-panel data-pipeline visualiser.
 *
 * Top: a listbox of every front panel. Left: that panel's pipeline drawn as
 * container blocks + labelled arrows (source → frontend). Right: the parent
 * voldesk view rendered LIVE (inside a local DataProvider), so the panel is
 * shown with real data next to its plumbing.
 *
 * `/dev` is provider-free by design; the live view is gated behind an explicit
 * "render" toggle so no data feeds open until the user asks for them.
 */
import { useState } from "react";
import { DataProvider } from "../../voldesk/data/provider";
import { DashboardView } from "../../voldesk/views/DashboardView";
import { PortfolioView } from "../../voldesk/views/PortfolioView";
import { RiskView } from "../../voldesk/views/RiskView";
import { SignalsView } from "../../voldesk/views/SignalsView";
import { SystemView } from "../../voldesk/views/SystemView";
import { TradeView } from "../../voldesk/views/TradeView";
import { PIPELINES, type NodeKind, type PanelPipe, type ViewId } from "./pipelines";

// Dashboard + Trade take props in the real app; stub them here (the pipeline
// viz only needs the panel rendered, not its nav / density wiring).
const VIEW_COMPONENTS: Record<ViewId, () => JSX.Element> = {
  signals: SignalsView,
  trade: () => <TradeView tweaks={{ density: "comfortable", showGreeks: true }} />,
  portfolio: PortfolioView,
  risk: RiskView,
  dashboard: () => <DashboardView go={() => undefined} />,
  system: SystemView,
};

const VIEW_LABEL: Record<ViewId, string> = {
  signals: "Signals",
  trade: "Trade",
  portfolio: "Portfolio",
  risk: "Risk",
  dashboard: "Dashboard",
  system: "System",
};

const KIND_STYLE: Record<NodeKind, { bg: string; border: string; tag: string }> = {
  external: { bg: "#3a2a26", border: "#8a6a52", tag: "external" },
  container: { bg: "#1c2a3c", border: "#3f6296", tag: "container" },
  store: { bg: "#27223f", border: "#5a4a96", tag: "store" },
  api: { bg: "#16332f", border: "#3a8f7c", tag: "api" },
  frontend: { bg: "#1d3a23", border: "#4d9460", tag: "frontend" },
};

function NodeBox({ node }: { node: PanelPipe["nodes"][number] }): JSX.Element {
  const s = KIND_STYLE[node.kind];
  return (
    <div style={{ background: s.bg, border: `1px solid ${s.border}`, borderRadius: 6, padding: "8px 12px", width: 320, maxWidth: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "#eee" }}>{node.label}</span>
        <span style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: 0.5, color: s.border }}>{s.tag}</span>
      </div>
      {node.sub ? <div style={{ fontSize: 11, color: "#9aa", marginTop: 2, fontFamily: "monospace" }}>{node.sub}</div> : null}
    </div>
  );
}

function Arrow({ label }: { label: string }): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, paddingLeft: 18, height: 34 }}>
      <span style={{ color: "#667", fontSize: 18, lineHeight: 1 }}>↓</span>
      <span style={{ fontSize: 11, color: "#8a93a3", fontFamily: "monospace" }}>{label}</span>
    </div>
  );
}

function Diagram({ pipe }: { pipe: PanelPipe }): JSX.Element {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {pipe.nodes.map((n, i) => (
        <div key={i}>
          <NodeBox node={n} />
          {i < pipe.nodes.length - 1 ? <Arrow label={pipe.edges[i] ?? ""} /> : null}
        </div>
      ))}
    </div>
  );
}

export function PipelineViz(): JSX.Element {
  const [id, setId] = useState<string>(PIPELINES[0]?.id ?? "");
  const [live, setLive] = useState(false);
  const pipe = PIPELINES.find((p) => p.id === id) ?? PIPELINES[0]!;
  const ViewComp = VIEW_COMPONENTS[pipe.view];

  // group panels by view for the <select> optgroups
  const byView = new Map<ViewId, PanelPipe[]>();
  for (const p of PIPELINES) byView.set(p.view, [...(byView.get(p.view) ?? []), p]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 92px)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", borderBottom: "1px solid #333", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "#aaa" }}>Panel</span>
        <select
          value={id}
          onChange={(e) => setId(e.target.value)}
          style={{ background: "#1a1a1a", color: "#ddd", border: "1px solid #444", borderRadius: 4, padding: "5px 8px", fontSize: 13, minWidth: 280 }}
        >
          {[...byView.entries()].map(([view, panels]) => (
            <optgroup key={view} label={VIEW_LABEL[view]}>
              {panels.map((p) => <option key={p.id} value={p.id}>{p.panel}</option>)}
            </optgroup>
          ))}
        </select>
        {pipe.endpoint ? <span style={{ fontSize: 12, color: "#7c9", fontFamily: "monospace" }}>{pipe.endpoint}</span> : null}
      </div>

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* left — pipeline diagram */}
        <div style={{ flex: "0 0 46%", overflow: "auto", padding: 18, borderRight: "1px solid #333" }}>
          <div style={{ fontSize: 12, color: "#888", marginBottom: 14, textTransform: "uppercase", letterSpacing: 1 }}>Pipeline</div>
          <Diagram pipe={pipe} />
        </div>

        {/* right — live panel (its parent view), gated behind a toggle */}
        <div style={{ flex: 1, overflow: "auto", background: "#0f1115", position: "relative" }}>
          {live ? (
            <DataProvider key={pipe.view}>
              <ViewComp />
            </DataProvider>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 12, color: "#888" }}>
              <div style={{ fontSize: 13 }}>Live <b style={{ color: "#bbb" }}>{VIEW_LABEL[pipe.view]}</b> view (contains this panel)</div>
              <button
                type="button"
                onClick={() => setLive(true)}
                style={{ background: "#2a4a6a", color: "#fff", border: "none", borderRadius: 4, padding: "8px 16px", cursor: "pointer", fontSize: 13 }}
              >
                ▶ Render live panel (opens data feeds)
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
