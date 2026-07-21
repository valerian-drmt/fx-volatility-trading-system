/**
 * Dev console — page unique `/dev`.
 *
 * All tabs are **mounted at startup and stay mounted**: their
 * fetch / WS / polling loops run from load time and keep going in the
 * background while switching. The tab button only toggles visibility
 * via CSS (`display: none` on inactive tabs), no mount/unmount.
 *
 * Intended consequences:
 *   - Fast clicks between tabs → instant, no re-fetch
 *   - The WS Monitor buffer keeps its messages when coming back to it
 *   - The EngineHealth polling keeps running even while looking at Redis
 *   - At a cost: N permanent fetches/WS — accepted for a local dev
 *     tool (the stack handles it easily)
 *
 * Constant /dev URL, never changes.
 */
import { useState, type CSSProperties } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Header } from "../components/layout/Header";
import { DbExplorer } from "./dev/DbExplorer";
import { DbSchema } from "./dev/DbSchema";
import { Hardware } from "./dev/Hardware";
import { Logs } from "./dev/Logs";
import { Migrations } from "./dev/Migrations";
import { PipelineViz } from "./dev/PipelineViz";
import { StackCombined } from "./dev/StackCombined";

interface TabDef {
  id: string;
  label: string;
  Component: () => JSX.Element;
}

const TABS: TabDef[] = [
  { id: "stack", label: "🐳 Stack · Health · Redis", Component: StackCombined },
  { id: "db", label: "🗃 DB Explorer", Component: DbExplorer },
  { id: "schema", label: "🗺 DB Schema", Component: DbSchema },
  { id: "logs", label: "🔍 Logs", Component: Logs },
  { id: "migrations", label: "🔁 Migrations", Component: Migrations },
  { id: "pipeline", label: "🧭 Pipeline", Component: PipelineViz },
  { id: "hw", label: "🖥 Hardware", Component: Hardware },
];

export function DevLayout(): JSX.Element {
  // Tab visible by default: the first one in the list.
  const [activeId, setActiveId] = useState<string>(TABS[0]?.id ?? "");

  return (
    <div className="app-shell" style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <Header />
      <nav style={navBarStyle}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setActiveId(t.id)}
            style={tabBtnStyle(t.id === activeId)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main style={{ flex: 1, overflow: "auto", background: "#0e0e0e", color: "#ddd", position: "relative" }}>
        {/*
          All components are rendered permanently. The inactive ones are
          hidden with display:none — they keep their internal state, their
          open WS connections, their polling, etc.
        */}
        {TABS.map((t) => (
          <div
            key={t.id}
            style={{ display: t.id === activeId ? "block" : "none" }}
          >
            {/*
              Per-tab boundary: since every tab is mounted at once, an
              unguarded throw in any one of them would otherwise unmount the
              whole console. Contained here, a broken tab shows its own error
              and the other six keep running.
            */}
            <ErrorBoundary label={t.label}>
              <t.Component />
            </ErrorBoundary>
          </div>
        ))}
      </main>
    </div>
  );
}

const navBarStyle = {
  display: "flex",
  gap: 4,
  padding: "6px 12px",
  background: "#222",
  borderBottom: "1px solid #333",
  fontSize: 13,
  flexWrap: "wrap" as const,
  overflowX: "auto" as const,
};

function tabBtnStyle(active: boolean): CSSProperties {
  return {
    padding: "6px 12px",
    color: active ? "#fff" : "#aaa",
    background: active ? "#2a4a6a" : "transparent",
    border: "none",
    borderRadius: 3,
    cursor: "pointer",
    fontSize: 13,
    whiteSpace: "nowrap",
  };
}
