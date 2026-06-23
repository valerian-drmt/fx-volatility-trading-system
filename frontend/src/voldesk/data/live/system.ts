/**
 * Live adapter (R11 PR 2r.1): backend health/engine probes → the voldesk System
 * view (container stack + engine heartbeats).
 *
 * Sources (composed front-side, per the planning doc — no dedicated topology
 * endpoint):
 *   - GET /health/extended  public readiness probe → redis/db/engine statuses
 *   - GET /dev/engines       richer per-engine heartbeat ages (5 engines + IB).
 *     ⚠️ /dev/* is auth-gated in the public deployment (see deployment memory),
 *     so this may fail; we degrade to /health/extended-only and the engine list
 *     falls back to the 3 health-probed engines (status, no heartbeat age).
 *
 * The ER-schema panel stays on the static mock (no live source).
 */
import type { EngineRow, StackLayer, SystemData } from "../deskData";

type Status = "up" | "warn" | "down";

function mapStatus(s: unknown): Status {
  if (s === "OK") return "up";
  if (s === "STALE" || s === "DEGRADED" || s === "WARN") return "warn";
  return "down"; // DOWN / missing / unknown
}

// backend engine key → desk label + one-line role.
const ENGINE_META: Record<string, { label: string; meta: string }> = {
  market_data: { label: "market-data", meta: "ticks · OHLC bars" },
  vol_engine: { label: "vol-engine", meta: "surface · SVI calib" },
  risk_engine: { label: "risk-engine", meta: "greeks · stress" },
  db_writer: { label: "db-writer", meta: "batch insert · Postgres" },
  execution: { label: "exec-engine", meta: "IB · orders" },
};
const ENGINE_META_BY_LABEL: Record<string, string> = Object.fromEntries(
  Object.values(ENGINE_META).map((m) => [m.label, m.meta]),
);

interface HealthExtendedResp {
  status?: string;
  components?: { redis?: string; database?: string; engines?: Record<string, string> };
}
interface DevEngine {
  name: string;
  status: string;
  hb_age_s?: number | null;
  stale_threshold_s?: number | null;
}
interface DevEnginesResp {
  engines?: DevEngine[];
  ib_gateway?: { status?: string } | null;
}

/** Engine heartbeat rows — prefer /dev/engines (ages + 5 engines + IB), else
 * derive from /health/extended (status only). */
function engineRows(health: HealthExtendedResp, dev: DevEnginesResp | null): EngineRow[] {
  if (dev?.engines?.length) {
    const rows: EngineRow[] = dev.engines.map((e) => {
      const stale = e.stale_threshold_s ?? 30;
      const hb = typeof e.hb_age_s === "number" ? e.hb_age_s : 0;
      return {
        name: ENGINE_META[e.name]?.label ?? e.name,
        hb,
        stale,
        status: mapStatus(e.status),
      };
    });
    if (dev.ib_gateway) {
      rows.push({ name: "IB Gateway", hb: 0, stale: 15, status: mapStatus(dev.ib_gateway.status) });
    }
    return rows;
  }
  // Fallback: health probes only market_data / vol_engine / risk_engine.
  const probed = health.components?.engines ?? {};
  return Object.entries(probed).map(([name, st]) => ({
    name: ENGINE_META[name]?.label ?? name,
    hb: 0,
    stale: 1,
    status: mapStatus(st),
  }));
}

/** Compose the 5-layer container stack from the live component statuses.
 * Edge/web/observability have no backend signal → "up" when the app is served. */
function stackLayers(health: HealthExtendedResp, dev: DevEnginesResp | null): StackLayer[] {
  const c = health.components ?? {};
  const eng = engineRows(health, dev).map((e) => ({
    name: e.name,
    status: e.status,
    meta: ENGINE_META_BY_LABEL[e.name] ?? `${e.hb}s / ${e.stale}s`,
  }));
  return [
    { layer: "EDGE", items: [{ name: "nginx", status: "up", meta: "reverse proxy · TLS" }] },
    {
      layer: "APP",
      items: [
        { name: "api (FastAPI)", status: mapStatus(health.status === "DEGRADED" ? "WARN" : "OK"), meta: "REST + WS" },
        { name: "web (Vite)", status: "up", meta: "React 18 · served" },
      ],
    },
    { layer: "ENGINES", items: eng },
    {
      layer: "DATA",
      items: [
        { name: "postgres", status: mapStatus(c.database), meta: "ORM · alembic" },
        { name: "redis", status: mapStatus(c.redis), meta: "pub/sub · cache" },
      ],
    },
    { layer: "OBS", items: [{ name: "AWS SSM/KMS", status: "up", meta: "secrets · encrypted" }] },
  ];
}

export function adaptSystem(healthRaw: unknown, devRaw: unknown): SystemData {
  const health = (healthRaw ?? {}) as HealthExtendedResp;
  const dev = (devRaw ?? null) as DevEnginesResp | null;
  return { engines: engineRows(health, dev), stack: stackLayers(health, dev) };
}
