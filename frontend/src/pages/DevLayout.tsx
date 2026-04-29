/**
 * Dev console — page unique `/dev`. URL ne change jamais ; le contenu
 * dépend d'un useState local.
 *
 *   - Au load : la bande d'onglets est visible, contenu vide (welcome).
 *   - Click sur un onglet → seul ce composant monte et lance ses fetch/WS.
 *   - Click sur un autre → l'ancien unmount (WS fermés, polling stoppés),
 *     le nouveau monte. Pas de partage d'état entre onglets.
 *
 * Ce design est volontaire : on veut **isoler** chaque interaction pour
 * voir précisément ce que cet onglet déclenche, sans que le bruit des
 * autres pollue le panel Network du browser.
 */
import { useState, type CSSProperties } from "react";
import { Header } from "../components/layout/Header";
import { EngineHealth } from "./dev/EngineHealth";
import { RedisInspector } from "./dev/RedisInspector";
import { WsMonitor } from "./dev/WsMonitor";

interface TabDef {
  id: string;
  label: string;
  Component: () => JSX.Element;
}

const TABS: TabDef[] = [
  { id: "health", label: "🩺 Engine Health", Component: EngineHealth },
  { id: "redis", label: "🔴 Redis Inspector", Component: RedisInspector },
  { id: "ws", label: "📡 WS Monitor", Component: WsMonitor },
  // Les prochaines sections (DB / Vol / Pricing / Trade Preview / Signals
  // / Orders) seront ajoutées ici au fur et à mesure.
];

export function DevLayout(): JSX.Element {
  const [activeId, setActiveId] = useState<string | null>(null);
  const active = TABS.find((t) => t.id === activeId) ?? null;

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
      <main style={{ flex: 1, overflow: "auto", background: "#0e0e0e", color: "#ddd" }}>
        {active ? <active.Component /> : <Welcome />}
      </main>
    </div>
  );
}

function Welcome(): JSX.Element {
  return (
    <section className="panel" style={{ margin: 16 }}>
      <header className="panel-header"><h2>fxvol — dev console</h2></header>
      <div className="panel-body" style={{ padding: 16 }}>
        Click un onglet ci-dessus pour ouvrir un panel de validation. Un
        seul panel monte à la fois — pas de fetch/WS en arrière-plan tant
        que tu n'as rien sélectionné.
      </div>
    </section>
  );
}

const navBarStyle = {
  display: "flex",
  gap: 4,
  padding: "6px 12px",
  background: "#222",
  borderBottom: "1px solid #333",
  fontSize: 13,
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
