/**
 * Dev console layout (R9 sandbox) — sidebar à gauche listant les 9 onglets de
 * validation + zone de contenu à droite. Le routing reste path-based (pas de
 * react-router pour cohérence avec routes.tsx existant) : on dispatche selon
 * le suffixe de l'URL `/dev/<tool>`.
 *
 * Quand un onglet n'est pas encore codé, on affiche le DevPlaceholder.
 */
import { DevPlaceholder } from "./dev/DevPlaceholder";

interface TabDef {
  path: string;       // suffixe de l'URL (après /dev/)
  label: string;      // texte du lien
  icon: string;       // emoji (placeholder, pas critique)
  group: string;      // séparateur dans la sidebar
}

const TABS: TabDef[] = [
  { path: "redis", label: "Redis Inspector", icon: "🔴", group: "Backend" },
  { path: "ws", label: "WS Monitor", icon: "📡", group: "Backend" },
  { path: "health", label: "Engine Health", icon: "🩺", group: "Backend" },
  { path: "db", label: "DB Explorer", icon: "🗃", group: "Backend" },
  { path: "vol", label: "Vol Surface", icon: "🌊", group: "API" },
  { path: "pricing", label: "Pricing", icon: "💲", group: "API" },
  { path: "trade-preview", label: "Trade Preview", icon: "📦", group: "API" },
  { path: "signals", label: "Signals", icon: "📈", group: "API" },
  { path: "orders", label: "Order Submit", icon: "📝", group: "Ops" },
];

function currentTab(): string {
  if (typeof window === "undefined") return "";
  const m = window.location.pathname.match(/^\/dev\/([^/?#]+)/);
  return m ? m[1] : "";
}

function TabContent({ tab }: { tab: string }): JSX.Element {
  const def = TABS.find((t) => t.path === tab);
  if (!def) {
    return (
      <section className="panel" style={{ margin: 16 }}>
        <header className="panel-header"><h2>Welcome — fxvol dev console</h2></header>
        <div className="panel-body" style={{ padding: 16 }}>
          Pick a tab on the left. Tous les onglets sont des stubs (TODO) jusqu'à
          ce qu'ils soient codés un par un. Cf. <code>releases/r9-frontend-
          validation-today-plan.md</code>.
        </div>
      </section>
    );
  }
  return <DevPlaceholder name={def.label} />;
}

export function DevLayout(): JSX.Element {
  const tab = currentTab();

  // Group tabs by category for the sidebar
  const groups = TABS.reduce<Record<string, TabDef[]>>((acc, t) => {
    (acc[t.group] ||= []).push(t);
    return acc;
  }, {});

  return (
    <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", minHeight: "100vh" }}>
      <aside
        style={{
          background: "#1a1a1a",
          color: "#ddd",
          padding: "12px 8px",
          borderRight: "1px solid #333",
          fontFamily: "system-ui, sans-serif",
          fontSize: 13,
        }}
      >
        <div style={{ padding: "0 8px 12px", fontWeight: 600, fontSize: 14, color: "#fff" }}>
          fxvol dev console
        </div>
        <a
          href="/"
          style={{ display: "block", padding: "6px 8px", color: "#7af", textDecoration: "none" }}
        >
          ← Live Dashboard
        </a>
        {Object.entries(groups).map(([group, tabs]) => (
          <div key={group} style={{ marginTop: 12 }}>
            <div style={{ padding: "4px 8px", color: "#666", fontSize: 11, textTransform: "uppercase" }}>
              {group}
            </div>
            {tabs.map((t) => {
              const active = t.path === tab;
              return (
                <a
                  key={t.path}
                  href={`/dev/${t.path}`}
                  style={{
                    display: "block",
                    padding: "6px 8px",
                    color: active ? "#fff" : "#aaa",
                    background: active ? "#2a4a6a" : "transparent",
                    textDecoration: "none",
                    borderRadius: 3,
                  }}
                >
                  <span style={{ marginRight: 6 }}>{t.icon}</span>
                  {t.label}
                </a>
              );
            })}
          </div>
        ))}
      </aside>
      <main style={{ overflow: "auto" }}>
        <TabContent tab={tab} />
      </main>
    </div>
  );
}
