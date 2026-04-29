/**
 * Dev console layout (R9 sandbox).
 *
 * - Top bar (Live / Dev) → vit dans Header.tsx (partagé avec App.tsx)
 * - Sub-tabs (9 surfaces de validation) → barre horizontale ici
 * - Content → DevPlaceholder tant que l'onglet n'est pas codé
 *
 * Routing path-based (cf. main.tsx) : dispatche selon /dev/<tool>.
 */
import type { CSSProperties } from "react";
import { Header } from "../components/layout/Header";
import { DevPlaceholder } from "./dev/DevPlaceholder";

interface TabDef {
  path: string;
  label: string;
}

const TABS: TabDef[] = [
  { path: "redis", label: "🔴 Redis" },
  { path: "ws", label: "📡 WS Monitor" },
  { path: "health", label: "🩺 Health" },
  { path: "db", label: "🗃 DB" },
  { path: "vol", label: "🌊 Vol Surface" },
  { path: "pricing", label: "💲 Pricing" },
  { path: "trade-preview", label: "📦 Trade Preview" },
  { path: "signals", label: "📈 Signals" },
  { path: "orders", label: "📝 Orders" },
];

function currentTab(): string {
  if (typeof window === "undefined") return "";
  const m = window.location.pathname.match(/^\/dev\/([^/?#]+)/);
  return m?.[1] ?? "";
}

function TabContent({ tab }: { tab: string }): JSX.Element {
  const def = TABS.find((t) => t.path === tab);
  if (!def) {
    return (
      <section className="panel" style={{ margin: 16 }}>
        <header className="panel-header"><h2>fxvol — dev console</h2></header>
        <div className="panel-body" style={{ padding: 16 }}>
          Pick a tab dans la barre ci-dessus. Tous les onglets sont des stubs
          (TODO) jusqu'à ce qu'ils soient codés un par un. Cf.{" "}
          <code>releases/r9-frontend-validation-today-plan.md</code>.
        </div>
      </section>
    );
  }
  return <DevPlaceholder name={def.label} />;
}

export function DevLayout(): JSX.Element {
  const tab = currentTab();
  return (
    <div className="app-shell" style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <Header />
      <nav
        style={{
          display: "flex",
          gap: 2,
          padding: "6px 12px",
          background: "#222",
          borderBottom: "1px solid #333",
          overflowX: "auto",
          fontSize: 13,
        }}
      >
        {TABS.map((t) => (
          <a key={t.path} href={`/dev/${t.path}`} style={subTabStyle(t.path === tab)}>
            {t.label}
          </a>
        ))}
      </nav>
      <main style={{ flex: 1, overflow: "auto", background: "#0e0e0e", color: "#ddd" }}>
        <TabContent tab={tab} />
      </main>
    </div>
  );
}

function subTabStyle(active: boolean): CSSProperties {
  return {
    padding: "6px 12px",
    color: active ? "#fff" : "#999",
    background: active ? "#2a4a6a" : "transparent",
    textDecoration: "none",
    borderRadius: 3,
    whiteSpace: "nowrap",
  };
}
