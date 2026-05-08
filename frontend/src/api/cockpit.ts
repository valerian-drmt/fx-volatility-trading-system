import { apiGet, apiPost } from "./client";

export interface RegimeResponse {
  regime: string;
  probabilities: Record<string, number>;
  features: Record<string, number | null>;
  vrp_by_tenor: Record<string, number>;
  event_dampener: boolean;
  bootstrap: boolean;
}

export type PcaState = "stable" | "bootstrap" | "unstable" | "refit_in_progress";

export interface PcaSignalNode {
  z_score: number;
  raw_score: number;
  label: "CHEAP" | "FAIR" | "EXPENSIVE";
  actionable: boolean;
  actionable_reason: string | null;
  recommended_structure: string | null;
}

export interface PcaCoherence {
  all_coherent: boolean;
  contradictions: [string, string][];
}

export interface PcaStateResponse {
  state: PcaState;
  timestamp?: string | null;
  model_version: string | null;
  n_obs_in_fit?: number;
  fit_window_start?: string | null;
  fit_window_end?: string | null;
  variance_explained?: {
    pc1: number; pc2: number; pc3: number; cumulative: number;
  };
  loadings_stable?: { pc1: boolean; pc2: boolean; pc3: boolean };
  signals: Partial<Record<"pc1" | "pc2" | "pc3", PcaSignalNode>>;
  coherence?: PcaCoherence;
  diagnostics?: { reason?: string };
}

export interface LegItem {
  instrument: string;
  side: string;
  qty: number;
  strike: number | null;
  tenor: string;
  iv: number | null;
  premium_per_contract: number;
}

export interface TradePreviewResponse {
  structure: string;
  legs: LegItem[];
  net_vega: number;
  net_gamma: number;
  net_theta: number;
  net_delta: number;
  total_premium: number;
  bootstrap: boolean;
}

export interface ModelHealthResponse {
  vol_surfaces_count: number;
  svi_params_count: number;
  last_vol_surface_ts: string | null;
  pca_ready: boolean;
}

export const fetchRegime = () => apiGet<RegimeResponse>("/api/v1/vol/regime");

export const fetchPcaState = () =>
  apiGet<PcaStateResponse>("/api/v1/signals/pca/state");

export const fetchModelHealth = () =>
  apiGet<ModelHealthResponse>("/api/v1/vol/model-health");

export const fetchTradePreview = (body: {
  structure: string;
  tenor: string;
  side?: string;
  qty?: number;
  tenor_far?: string;
}) => apiPost<TradePreviewResponse>("/api/v1/vol/trade-preview", body);
