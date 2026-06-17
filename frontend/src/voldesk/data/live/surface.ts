/**
 * Live adapter (R11 PR 1): backend `VolSurface` payload → the 6×5 IV grid the
 * voldesk Signals heatmap consumes (`ivSurface[tenorIdx][deltaIdx]`, in %).
 *
 * Backend shape: `surface[tenor][delta].iv` (a fraction; `_`-prefixed keys are
 * meta). Delta keys are lowercase (10dp/25dp/atm/25dc/10dc); the voldesk labels
 * (10Δp…) are positional, so we map by index. Missing cells → 0.
 *
 * The per-cell z (`ivZ`) is carried by `/vol/surface` as
 * `surface[tenor][delta].z` (vol-engine `_attach_surface_z`): a cross-sectional
 * z = (iv_cell − mean)/std over the WHOLE current surface — shows the smile/term
 * shape + the 10Δp vs 10Δc skew. No history needed. `adaptIvZ` reads it.
 */
import type { VolSurface } from "../../../api/endpoints";

export const SURFACE_TENOR_KEYS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;
export const SURFACE_DELTA_KEYS = ["10dp", "25dp", "atm", "25dc", "10dc"] as const;

type Cell = { iv?: number | null; z?: number | null } | undefined;
type TenorMap = Partial<Record<string, Cell>> | undefined;

/** Extract the 6×5 IV grid (%) from the backend surface payload. */
export function adaptIvSurface(resp: VolSurface): number[][] {
  const surface = (resp as { surface?: Record<string, TenorMap> }).surface ?? {};
  return SURFACE_TENOR_KEYS.map((t) => {
    const row = surface[t];
    return SURFACE_DELTA_KEYS.map((d) => {
      const iv = row?.[d]?.iv;
      return typeof iv === "number" ? iv * 100 : 0;
    });
  });
}

/** Per-cell z grid (6×5) from the backend surface (cell `.z`). Cross-sectional
 * z = (iv_cell − mean)/std over the whole current surface (vol-engine
 * `_attach_surface_z`) — + = high vs surface (wings), − = low (ATM); 10Δp vs
 * 10Δc = put/call skew. Missing cell → 0 (neutral). */
export function adaptIvZ(resp: VolSurface): number[][] {
  const surface = (resp as { surface?: Record<string, TenorMap> }).surface ?? {};
  return SURFACE_TENOR_KEYS.map((t) => {
    const row = surface[t];
    return SURFACE_DELTA_KEYS.map((d) => {
      const z = row?.[d]?.z;
      return typeof z === "number" ? z : 0;
    });
  });
}
