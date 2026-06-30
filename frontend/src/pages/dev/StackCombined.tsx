/**
 * Onglet Stack — single column verticale, ordre top-down :
 *   1. 🐳 Stack schema (SVG)
 *   2. 🩺 Engine Health (cards)
 *   3. 🔴 Redis keys + value (table dessus, valeur dessous)
 *
 * Tout auto-refresh 3s. Pas de bouton refresh.
 */
import { EngineHealth } from "./EngineHealth";
import { RedisKeysPanel, RedisValuePanel, useRedisInspector } from "./RedisInspector";
import { StackOverview } from "./StackOverview";

export function StackCombined(): JSX.Element {
  const redis = useRedisInspector();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 12 }}>
      <Section title="🐳 Stack"><StackOverview /></Section>
      <Section title="🩺 Engine Health"><EngineHealth /></Section>
      <Section title="🔴 Redis keys"><RedisKeysPanel state={redis} /></Section>
      <Section title="🔴 Redis value"><RedisValuePanel state={redis} /></Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <div
      style={{
        background: "#0a0a0a",
        border: "1px solid #222",
        borderRadius: 4,
        overflow: "hidden",
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
      <div>{children}</div>
    </div>
  );
}
