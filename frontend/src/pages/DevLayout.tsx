/**
 * Dev console — page unique. Tous les panels de validation sont stackés
 * verticalement et tournent en parallèle au mount. Pas de sub-tabs : un
 * seul URL `/dev` qui montre tout. Permet de voir d'un coup d'œil l'état
 * de chaque interaction stack ↔ UI.
 *
 * Au mount :
 *   - RedisInspector fetch /api/v1/dev/redis/keys
 *   - WsMonitor ouvre 3 WS (/ws/ticks, /ws/vol, /ws/risk)
 *   - EngineHealth fetch /api/v1/dev/engines + auto-refresh 5s
 *   - (les futures sections feront pareil)
 *
 * Header reste partagé (Header.tsx) avec le bouton "← Live".
 */
import { Header } from "../components/layout/Header";
import { EngineHealth } from "./dev/EngineHealth";
import { RedisInspector } from "./dev/RedisInspector";
import { WsMonitor } from "./dev/WsMonitor";

interface SectionDef {
  id: string;
  title: string;
  Component: () => JSX.Element;
}

const SECTIONS: SectionDef[] = [
  { id: "health", title: "🩺 Engine Health", Component: EngineHealth },
  { id: "redis", title: "🔴 Redis Inspector", Component: RedisInspector },
  { id: "ws", title: "📡 WS Monitor", Component: WsMonitor },
  // Les prochaines sections (DB / Vol / Pricing / Trade Preview / Signals
  // / Orders) seront ajoutées ici au fur et à mesure des étapes.
];

export function DevLayout(): JSX.Element {
  return (
    <div className="app-shell" style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <Header />
      <nav style={navBarStyle}>
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`} style={navLinkStyle}>
            {s.title}
          </a>
        ))}
      </nav>
      <main style={{ flex: 1, overflow: "auto", background: "#0e0e0e", color: "#ddd" }}>
        {SECTIONS.map((s) => (
          <section key={s.id} id={s.id} style={sectionStyle}>
            <h2 style={sectionTitleStyle}>{s.title}</h2>
            <s.Component />
          </section>
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
  position: "sticky" as const,
  top: 0,
  zIndex: 10,
};

const navLinkStyle = {
  padding: "4px 12px",
  color: "#aaa",
  textDecoration: "none",
  borderRadius: 3,
};

const sectionStyle = {
  borderTop: "2px solid #2a4a6a",
  paddingTop: 4,
};

const sectionTitleStyle = {
  margin: 0,
  padding: "10px 16px 6px",
  fontSize: 14,
  color: "#7af",
  background: "#1a1a1a",
  borderBottom: "1px solid #333",
};
