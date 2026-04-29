/**
 * Onglet Stack — vue combinée 3 colonnes :
 *   Col 1 : 🩺 Engine Health (cards par container)
 *   Col 2 : 🐳 Stack schema  (SVG draw.io-style)
 *   Col 3 : 🔴 Redis Inspector (table en haut, valeur en bas)
 *
 * Chaque sous-composant garde son propre fetch / auto-refresh — pas de
 * coordination, ils tournent en parallèle.
 */
import { EngineHealth } from "./EngineHealth";
import { RedisInspector } from "./RedisInspector";
import { StackOverview } from "./StackOverview";

export function StackCombined(): JSX.Element {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.4fr) minmax(0, 1fr)",
        gap: 8,
        padding: 8,
        height: "calc(100vh - 80px)",
        boxSizing: "border-box",
      }}
    >
      {/* Col 1 : Engine Health */}
      <div style={colStyle}>
        <ColTitle>🩺 Engine Health</ColTitle>
        <div style={{ overflow: "auto", flex: 1 }}>
          <EngineHealth />
        </div>
      </div>

      {/* Col 2 : Stack schema */}
      <div style={colStyle}>
        <ColTitle>🐳 Stack</ColTitle>
        <div style={{ overflow: "auto", flex: 1 }}>
          <StackOverview />
        </div>
      </div>

      {/* Col 3 : Redis Inspector (sa propre 2-row interne keys/value) */}
      <div style={colStyle}>
        <ColTitle>🔴 Redis Inspector</ColTitle>
        <div style={{ overflow: "auto", flex: 1 }}>
          <RedisInspector />
        </div>
      </div>
    </div>
  );
}

function ColTitle({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div style={{
      padding: "6px 12px",
      background: "#1a1a1a",
      borderBottom: "1px solid #333",
      color: "#7af",
      fontSize: 12,
      fontWeight: 600,
      letterSpacing: 1,
    }}>{children}</div>
  );
}

const colStyle = {
  display: "flex",
  flexDirection: "column" as const,
  background: "#0a0a0a",
  border: "1px solid #222",
  borderRadius: 4,
  overflow: "hidden",
  minHeight: 0,
};
