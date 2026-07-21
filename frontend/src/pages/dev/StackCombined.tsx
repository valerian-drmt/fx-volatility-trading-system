/**
 * Onglet Stack — single column verticale, ordre top-down :
 *   1. 🐳 Stack schema (SVG)              — public
 *   2. 🩺 Engine Health (cards)           — public
 *   3. 🔴 Redis keys + value              — TRADER only (write-gated endpoints)
 *
 * Stack + Health are the public showcase; the Redis inspector hits the
 * write-gated /dev/redis/* endpoints, so it renders only when logged in (and
 * useRedisInspector is called inside RedisSection so it doesn't poll a 401 for
 * public visitors). See releases/PLAN_trader_vs_public.md.
 *
 * Tout auto-refresh 3s. Pas de bouton refresh.
 */
import { useAuthStore } from "../../store/authStore";
import { EngineHealth } from "./EngineHealth";
import { RedisKeysPanel, RedisValuePanel, useRedisInspector } from "./RedisInspector";
import { StackOverview } from "./StackOverview";

export function StackCombined(): JSX.Element {
  const authenticated = useAuthStore((s) => s.authenticated);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 12 }}>
      <Section title="🐳 Stack"><StackOverview /></Section>
      <Section title="🩺 Engine Health"><EngineHealth /></Section>
      {authenticated ? (
        <RedisSection />
      ) : (
        <Section title="🔴 Redis">
          <div style={{ padding: "10px 12px", color: "#888", fontSize: 13 }}>
            Log in as trader to inspect Redis keys and values.
          </div>
        </Section>
      )}
    </div>
  );
}

// Isolated so useRedisInspector (which polls the write-gated /dev/redis/*
// endpoints) only runs when a trader is logged in.
function RedisSection(): JSX.Element {
  const redis = useRedisInspector();
  return (
    <>
      <Section title="🔴 Redis keys"><RedisKeysPanel state={redis} /></Section>
      <Section title="🔴 Redis value"><RedisValuePanel state={redis} /></Section>
    </>
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
