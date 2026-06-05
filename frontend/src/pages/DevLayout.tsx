/**
 * Dev console — page unique `/dev`.
 *
 * Tous les onglets sont **mountés au démarrage et restent mountés** : leurs
 * fetch / WS / pollings tournent dès le load et continuent en arrière-plan
 * pendant qu'on switche. Le bouton d'onglet ne fait que toggle la visibilité
 * via CSS (`display: none` sur les inactifs), pas mount/unmount.
 *
 * Conséquences voulues :
 *   - Click rapide entre onglets → instantané, pas de re-fetch
 *   - Le buffer WS Monitor garde ses messages quand on revient dessus
 *   - Le polling EngineHealth continue même si on regarde Redis
 *   - Au cost : N fetches/WS en permanence — assumé pour un dev tool
 *     local (la stack supporte largement)
 *
 * URL constant /dev, jamais de changement.
 */
import { useState, type CSSProperties } from "react";
import { Header } from "../components/layout/Header";
import { DbExplorer } from "./dev/DbExplorer";
import { DbSchema } from "./dev/DbSchema";
import { Logs } from "./dev/Logs";
import { Migrations } from "./dev/Migrations";
import { Portfolio } from "./dev/Portfolio";
import { StackCombined } from "./dev/StackCombined";
import { Step2Pca } from "./dev/Step2Pca";
import { Step3Trade } from "./dev/Step3Trade";
// Step4Trades / TradePreview / OrderSubmit were folded into Step3Trade
// (unified "Trade · pre/post" tab). Their source files remain on disk
// for cherry-picking history but are no longer imported.
import { WsMonitor } from "./dev/WsMonitor";

interface TabDef {
  id: string;
  label: string;
  Component: () => JSX.Element;
}

const TABS: TabDef[] = [
  { id: "stack", label: "🐳 Stack · Health · Redis", Component: StackCombined },
  { id: "ws", label: "📡 WS Monitor", Component: WsMonitor },
  { id: "db", label: "🗃 DB Explorer", Component: DbExplorer },
  { id: "schema", label: "🗺 DB Schema", Component: DbSchema },
  { id: "logs", label: "🔍 Logs", Component: Logs },
  { id: "migrations", label: "🔁 Migrations", Component: Migrations },
  { id: "step2", label: "📊 PCA Signals", Component: Step2Pca },
  { id: "step3", label: "🎯 Trade · pre/post", Component: Step3Trade },
  { id: "portfolio", label: "💼 Portfolio", Component: Portfolio },
];

export function DevLayout(): JSX.Element {
  // Onglet visible par défaut : le premier de la liste.
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
          Tous les composants sont rendus en permanence. On masque les
          inactifs avec display:none — ils gardent leur state interne, leurs
          WS connectées, leur polling, etc.
        */}
        {TABS.map((t) => (
          <div
            key={t.id}
            style={{ display: t.id === activeId ? "block" : "none" }}
          >
            <t.Component />
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
