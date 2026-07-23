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
import { useEffect, useState, type CSSProperties } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Header } from "../components/layout/Header";
import { useAuthStore } from "../store/authStore";
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
  // trader-only tab: hidden from the public showcase, shown once logged in.
  // Mirrors the backend split (see releases/PLAN_trader_vs_public.md): the
  // Stack/Schema/Migrations/Pipeline/Hardware tabs are a safe read-only
  // showcase; the DB Explorer / Logs tabs are the write-gated debug tools.
  trader?: boolean;
}

const TABS: TabDef[] = [
  { id: "stack", label: "🐳 Stack · Health", Component: StackCombined },
  { id: "schema", label: "🗺 DB Schema", Component: DbSchema },
  { id: "migrations", label: "🔁 Migrations", Component: Migrations },
  { id: "pipeline", label: "🧭 Pipeline", Component: PipelineViz },
  { id: "db", label: "🗃 DB Explorer", Component: DbExplorer, trader: true },
  { id: "logs", label: "🔍 Logs", Component: Logs, trader: true },
  { id: "hw", label: "🖥 Hardware", Component: Hardware },
];

export function DevLayout(): JSX.Element {
  const authenticated = useAuthStore((s) => s.authenticated);
  const refresh = useAuthStore((s) => s.refresh);
  // /dev is a separate full-page entry (main.tsx path routing → fresh zustand
  // store), so unlike the desk (VoldeskApp) nothing probes /me here. Without
  // this the httpOnly cookie set on the desk is ignored and `authenticated`
  // stays false forever → the trader tabs never appear. Probe /me once on mount.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Public sees the showcase tabs; a logged-in trader sees everything.
  const tabs = TABS.filter((t) => !t.trader || authenticated);

  // Default to the first visible tab; if the active one just got hidden (e.g.
  // logged out while on a trader tab), fall back to the first visible.
  const [activeId, setActiveId] = useState<string>(tabs[0]?.id ?? "");
  const effectiveId = tabs.some((t) => t.id === activeId) ? activeId : (tabs[0]?.id ?? "");

  return (
    <div className="app-shell" style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <Header />
      <nav style={navBarStyle}>
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setActiveId(t.id)}
            style={tabBtnStyle(t.id === effectiveId)}
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
        {tabs.map((t) => (
          <div
            key={t.id}
            style={{ display: t.id === effectiveId ? "block" : "none" }}
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
