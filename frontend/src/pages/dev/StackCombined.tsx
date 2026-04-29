/**
 * Onglet Stack — vue combinée 2 lignes :
 *
 *   Row 1 :  [ EngineHealth (33%) ] [ StackOverview SVG (66%) ]
 *   Row 2 :  [ Redis keys table (50%) ] [ Redis value pane (50%) ]
 *
 * Row 2 partage une seule instance de useRedisInspector → click sur une
 * key dans la table met à jour la value pane juste à côté.
 */
import { EngineHealth } from "./EngineHealth";
import { RedisKeysPanel, RedisValuePanel, useRedisInspector } from "./RedisInspector";
import { StackOverview } from "./StackOverview";

export function StackCombined(): JSX.Element {
  const redis = useRedisInspector();

  return (
    <div
      style={{
        display: "grid",
        // Row 1 = "auto" → s'étire pour afficher le SVG entier sans overflow.
        // Row 2 = remplit le reste de la fenêtre (avec un min de 250px pour
        // garder Redis utilisable même sur petit écran).
        gridTemplateRows: "auto minmax(250px, 1fr)",
        gap: 8,
        padding: 8,
        boxSizing: "border-box",
      }}
    >
      {/* Row 1 : Engine Health (33%) + Stack schema (66%) */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 8 }}>
        <Cell title="🩺 Engine Health">
          <EngineHealth />
        </Cell>
        <Cell title="🐳 Stack">
          <StackOverview />
        </Cell>
      </div>

      {/* Row 2 : Redis keys (50%) + Value pane (50%) — état partagé */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, minHeight: 0 }}>
        <Cell title="🔴 Redis keys">
          <RedisKeysPanel state={redis} />
        </Cell>
        <Cell title="🔴 Redis value">
          <RedisValuePanel state={redis} />
        </Cell>
      </div>
    </div>
  );
}

function Cell({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        background: "#0a0a0a",
        border: "1px solid #222",
        borderRadius: 4,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <div
        style={{
          padding: "5px 12px",
          background: "#1a1a1a",
          borderBottom: "1px solid #333",
          color: "#7af",
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: 1,
        }}
      >
        {title}
      </div>
      <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>{children}</div>
    </div>
  );
}
