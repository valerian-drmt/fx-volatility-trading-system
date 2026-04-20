// Typed helpers built on top of the generated OpenAPI schema. Any drift between
// FastAPI Pydantic models and `schema.d.ts` is caught at `tsc --noEmit` time
// (or earlier by `npm run gen:api:check` in CI).
import type { paths } from "./schema";
import { apiGet, apiPost } from "./client";

type Get<P extends keyof paths, S extends 200> = paths[P] extends {
  get: { responses: { [K in S]: { content: { "application/json": infer R } } } };
}
  ? R
  : never;

type Post<P extends keyof paths, S extends 200> = paths[P] extends {
  post: { responses: { [K in S]: { content: { "application/json": infer R } } } };
}
  ? R
  : never;

type PostBody<P extends keyof paths> = paths[P] extends {
  post: { requestBody: { content: { "application/json": infer B } } };
}
  ? B
  : never;

// ── Health ────────────────────────────────────────────────────────────────
export type Health = Get<"/api/v1/health", 200>;
export type HealthExtended = Get<"/api/v1/health/extended", 200>;
export const fetchHealth = () => apiGet<Health>("/api/v1/health");
export const fetchHealthExtended = () =>
  apiGet<HealthExtended>("/api/v1/health/extended");

// ── Pricing ───────────────────────────────────────────────────────────────
export type PriceRequest = PostBody<"/api/v1/price">;
export type PriceResponse = Post<"/api/v1/price", 200>;
export type GreeksResponse = Post<"/api/v1/greeks", 200>;
export type IvRequest = PostBody<"/api/v1/iv">;
export type IvResponse = Post<"/api/v1/iv", 200>;

export const postPrice = (body: PriceRequest) =>
  apiPost<PriceResponse>("/api/v1/price", body);
export const postGreeks = (body: PriceRequest) =>
  apiPost<GreeksResponse>("/api/v1/greeks", body);
export const postIv = (body: IvRequest) => apiPost<IvResponse>("/api/v1/iv", body);

// ── Vol ───────────────────────────────────────────────────────────────────
export type VolSurface = Get<"/api/v1/vol/surface", 200>;
export type TermStructure = Get<"/api/v1/vol/term-structure", 200>;
export type Smile = Get<"/api/v1/vol/smile/{tenor}", 200>;

export const fetchVolSurface = (symbol = "EURUSD") =>
  apiGet<VolSurface>("/api/v1/vol/surface", { query: { symbol } });
export const fetchTermStructure = (symbol = "EURUSD") =>
  apiGet<TermStructure>("/api/v1/vol/term-structure", { query: { symbol } });
export const fetchSmile = (tenor: string, symbol = "EURUSD") =>
  apiGet<Smile>(`/api/v1/vol/smile/${encodeURIComponent(tenor)}`, { query: { symbol } });

// ── Portfolio ─────────────────────────────────────────────────────────────
export type Positions = Get<"/api/v1/positions", 200>;
export type Position = Get<"/api/v1/positions/{position_id}", 200>;
export type Risk = Get<"/api/v1/risk", 200>;
export type PnlCurve = Get<"/api/v1/pnl-curve", 200>;

export const fetchPositions = (params?: { status?: string; limit?: number }) =>
  apiGet<Positions>("/api/v1/positions", params ? { query: params } : {});
export const fetchPosition = (id: number) =>
  apiGet<Position>(`/api/v1/positions/${id}`);
export const fetchRisk = () => apiGet<Risk>("/api/v1/risk");
export const fetchPnlCurve = () => apiGet<PnlCurve>("/api/v1/pnl-curve");

// ── Analytics ─────────────────────────────────────────────────────────────
export type Signals = Get<"/api/v1/signals", 200>;
export type VolHistory = Get<"/api/v1/vol-history", 200>;
export type SystemStats = Get<"/api/v1/system-stats", 200>;

export const fetchSignals = (params?: {
  underlying?: string;
  tenor?: string;
  signal_type?: string;
  limit?: number;
}) => apiGet<Signals>("/api/v1/signals", params ? { query: params } : {});

export const fetchVolHistory = (symbol = "EURUSD", limit = 50) =>
  apiGet<VolHistory>("/api/v1/vol-history", { query: { symbol, limit } });

export const fetchSystemStats = () => apiGet<SystemStats>("/api/v1/system-stats");
