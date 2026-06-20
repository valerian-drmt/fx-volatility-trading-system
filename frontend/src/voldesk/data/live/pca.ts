/**
 * Live adapter (R11 PR 1.2): backend PCA payloads → the voldesk Signals mode
 * cards + mode-stability panel.
 *
 * Sources:
 *   - GET /signals/pca/state   per-PC z/label/loadings + variance + coherence
 *   - GET /signals/pca/model   active-model meta (full variance_explained list)
 *   - GET /signals/pca/history per-PC z time-series (trajectory + percentile)
 *
 * Live fields : z, label, variance, loadings (6×5), stability, reco, coherence,
 * eigen (λ from variance_explained, gap λ2−λ3, ratio λ2/λ3), zHistory, pctile.
 *
 * ⚠️ Display-config fields the backend does NOT expose (pca/z windows, refit
 * cadence, dims, shrinkage) stay on static constants below — descriptive header
 * text only, flagged as a minor gap (releases/r11_frontend_backend/09). The
 * per-PC name/desc/thr are PCA-on-a-vol-surface invariants (front constants).
 */
import { pcaModel as mockModel } from "../core";
import type { PcaCard, PcaData, PcaModelMeta } from "../deskData";

interface PcSig {
  z_score: number;
  label: "CHEAP" | "FAIR" | "EXPENSIVE";
  sub_signals?: { skew_z?: number; convex_z?: number } | null;
}
interface PcaStateResp {
  signals?: { pc1?: PcSig; pc2?: PcSig; pc3?: PcSig };
  variance_explained?: { pc1: number; pc2: number; pc3: number; cumulative: number };
  loadings_stable?: { pc1?: boolean; pc2?: boolean; pc3?: boolean };
  loadings_grid?: number[][][];
  coherence?: { all_coherent: boolean; contradictions: [string, string][] };
}
interface PcaModelResp {
  n_obs_used?: number | null;
  variance_explained?: number[] | null;
}
type HistoryRow = { z_score?: number | null };

// PCA-on-a-vol-surface invariants: PC1=level, PC2=slope, PC3=curvature. thr =
// per-mode actionable z (display thresholds), matching the desk convention.
const PC_META = [
  { id: "PC1", key: "pc1", name: "level", desc: "surface up/down", thr: 1.5 },
  { id: "PC2", key: "pc2", name: "slope", desc: "front vs back (tenor)", thr: 1.8 },
  { id: "PC3", key: "pc3", name: "curvature", desc: "wings vs ATM (delta)", thr: 2.0 },
] as const;

// 6 tenors × 5 deltas zero grid — loadings fallback so the heatmap always has a
// well-formed matrix even when the model carries no loadings yet.
const ZERO_GRID: number[][] = Array.from({ length: 6 }, () => [0, 0, 0, 0, 0]);

/** percentile rank (%) of `v` within `xs` (share of |samples| ≤ v). */
function pctileRank(v: number, xs: number[]): number {
  if (xs.length === 0) return 0;
  const le = xs.filter((x) => x <= v).length;
  return (le / xs.length) * 100;
}

/**
 * Build the 3 mode cards + model meta from the live payloads. `histories[i]`
 * is the /signals/pca/history rows for PC(i+1), newest-first (as the API
 * returns them); we reverse for the trajectory and use the values for pctile.
 */
export function adaptPca(
  stateRaw: unknown,
  modelRaw: unknown,
  historiesRaw: unknown[],
): PcaData {
  const state = (stateRaw ?? {}) as PcaStateResp;
  const model = (modelRaw ?? {}) as PcaModelResp;
  const histories = historiesRaw as HistoryRow[][];
  const sig = state.signals ?? {};
  const ve = state.variance_explained;
  const grids = state.loadings_grid ?? [];
  const stable = state.loadings_stable ?? {};

  const pcs: PcaCard[] = PC_META.map((meta, i) => {
    const s = sig[meta.key as "pc1" | "pc2" | "pc3"];
    const z = s ? s.z_score : 0;
    const varPct = ve ? (ve[meta.key as "pc1" | "pc2" | "pc3"] ?? 0) * 100 : 0;
    const isStable = stable[meta.key as "pc1" | "pc2" | "pc3"] ?? true;
    const zHistory = (histories[i] ?? [])
      .map((r) => (typeof r.z_score === "number" ? r.z_score : null))
      .filter((x): x is number => x !== null)
      .reverse(); // API is newest-first → oldest→newest for the chart
    const convexZ = s?.sub_signals?.convex_z;
    return {
      id: meta.id,
      name: meta.name,
      desc: meta.desc,
      z,
      pctile: zHistory.length ? pctileRank(z, zHistory) : 0,
      label: s?.label ?? "FAIR",
      variance: varPct,
      stable: isStable,
      tier: i + 1, // conviction = variance rank
      dataQuality: isStable ? "clean" : "noisy",
      thr: meta.thr,
      load: grids[i] && grids[i]!.length ? grids[i]! : ZERO_GRID,
      extra: meta.id === "PC3" && typeof convexZ === "number" ? { convex_z: convexZ } : null,
      zHistory,
    };
  });

  // Eigen bars: variance_explained ratios ARE the normalised eigenvalues. Use
  // the full model list when present (all components), else the state's top-3.
  const lamFrac =
    model.variance_explained && model.variance_explained.length
      ? model.variance_explained
      : ve
        ? [ve.pc1, ve.pc2, ve.pc3]
        : [];
  const lambda = lamFrac.map((x) => x * 100);
  const l2 = lambda[1] ?? 0;
  const l3 = lambda[2] ?? 0;
  const gap23 = l2 - l3;
  const ratio23 = l3 > 0 ? l2 / l3 : 0;
  const coherent = state.coherence?.all_coherent ?? true;
  const contradictions = state.coherence?.contradictions ?? [];

  const modelMeta: PcaModelMeta = {
    ...mockModel, // inherits the display-config statics (windows, dims, shrinkage…)
    variance: {
      pc1: ve ? ve.pc1 * 100 : 0,
      pc2: ve ? ve.pc2 * 100 : 0,
      pc3: ve ? ve.pc3 * 100 : 0,
      cumul: ve ? ve.cumulative * 100 : 0,
    },
    coherence: coherent ? "aligned" : "contradictions",
    coherenceNote: coherent
      ? "no contradictions across PCs"
      : contradictions.map((c) => `${c[0]} vs ${c[1]}`).join(", "),
    pcaObs: model.n_obs_used ?? mockModel.pcaObs,
    stable: ratio23 >= 2,
    eigen: {
      lambda,
      gap23,
      ratio23,
      state: ratio23 < 2 ? "narrow" : "wide",
      note:
        ratio23 < 2
          ? "PC2/PC3 identities may rotate on refit"
          : "modes well separated",
    },
  };

  return { pcs, model: modelMeta };
}
