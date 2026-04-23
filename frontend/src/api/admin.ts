// Typed client for /api/v1/admin/config.
//
// Types are hand-written for T1 because the generated schema.d.ts is
// regenerated from a running API (`npm run gen:api`). When the backend
// admin router lands on main and someone runs gen:api, the schema
// types can replace these hand-written ones.
import { apiGet, apiPost, apiPut } from "./client";

// Minimal subset mirroring src/core/config/vol_params.py. Kept as plain
// `Record<string, unknown>` at the nested level because T1 only exposes
// threshold_vol_pts + model_p in the UI ; RJSF will take over the rest.
export interface SignalConfig {
  threshold_vol_pts: number;
  model_p: "har" | "garch" | "ewma";
  // Other fields exist but the T1 form doesn't render them yet.
  [key: string]: unknown;
}

export interface VolTradingConfig {
  signal: SignalConfig;
  regime: Record<string, unknown>;
  sizing: Record<string, unknown>;
  exit_rules: Record<string, unknown>;
  surface: Record<string, unknown>;
  calibration: Record<string, unknown>;
  delta_hedge: Record<string, unknown>;
  structures: Record<string, unknown>;
}

export interface ConfigResponse {
  version: number;
  config: VolTradingConfig;
  updated_at: string;
  updated_by: string | null;
  comment: string | null;
}

export interface ConfigPatchRequest {
  patch: Record<string, unknown>;
  user?: string | undefined;
  comment?: string | undefined;
}

export const fetchCurrentConfig = () =>
  apiGet<ConfigResponse>("/api/v1/admin/config");

export const fetchConfigSchema = () =>
  apiGet<Record<string, unknown>>("/api/v1/admin/config/schema");

export const putConfig = (body: ConfigPatchRequest) =>
  apiPut<ConfigResponse>("/api/v1/admin/config", body);

export const fetchConfigHistory = (limit = 50) =>
  apiGet<ConfigResponse[]>(`/api/v1/admin/config/history?limit=${limit}`);

export const revertConfig = (version: number, user?: string, comment?: string) =>
  apiPost<ConfigResponse>(`/api/v1/admin/config/revert/${version}`, {
    user,
    comment,
  });
