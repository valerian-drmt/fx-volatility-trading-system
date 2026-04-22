import { apiGet, apiPost } from "./client";

export interface RegimeResponse {
  regime: string;
  probabilities: Record<string, number>;
  features: Record<string, number | null>;
  vrp_by_tenor: Record<string, number>;
  event_dampener: boolean;
  bootstrap: boolean;
}

export interface PcSignalItem {
  pc: number;
  label: string;
  z_score: number;
  current: number;
  mean: number;
  std: number;
  bootstrap: boolean;
  recommended_structure: string | null;
  recommended_tenor: string | null;
}

export interface PcaSignalsResponse {
  timestamp: string | null;
  signals: PcSignalItem[];
  explained_variance: number[];
  n_samples_trained: number;
  bootstrap: boolean;
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
  signals_count: number;
  svi_params_count: number;
  last_vol_surface_ts: string | null;
  last_signal_ts: string | null;
  pca_ready: boolean;
  vrp_calibration_ready: boolean;
  fair_smile_ready: boolean;
}

export const fetchRegime = () => apiGet<RegimeResponse>("/api/v1/vol/regime");

export const fetchPcaSignals = () =>
  apiGet<PcaSignalsResponse>("/api/v1/vol/pca-signals");

export const fetchModelHealth = () =>
  apiGet<ModelHealthResponse>("/api/v1/vol/model-health");

export const fetchTradePreview = (body: {
  structure: string;
  tenor: string;
  side?: string;
  qty?: number;
  tenor_far?: string;
}) => apiPost<TradePreviewResponse>("/api/v1/vol/trade-preview", body);
