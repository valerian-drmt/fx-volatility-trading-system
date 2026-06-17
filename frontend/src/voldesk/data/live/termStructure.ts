/**
 * Pilot live adapter (R11 PR F): backend `TermStructureResponse` → voldesk
 * `TermPoint[]`. Proves the mock→live swap end-to-end on one typed domain.
 *
 * Mapping: atm←sigma_atm_pct, fair←sigma_fair_q_pct||sigma_fair_pct, rv←rv_pct
 * (horizon-matched per tenor), bf/rr←the surface-wing smile metrics computed
 * server-side (rr_25d_pct etc.). The bf/rr fields post-date the committed
 * schema.d.ts → read via a localized cast until the OpenAPI types regen.
 */
import type { TermStructure } from "../../../api/endpoints";
import type { TermPoint } from "../core";

interface SmileFields {
  rr_25d_pct?: number | null;
  bf_25d_pct?: number | null;
  rr_10d_pct?: number | null;
  bf_10d_pct?: number | null;
}

export function adaptTermStructure(resp: TermStructure): TermPoint[] {
  return resp.pillars.map((p) => {
    const s = p as typeof p & SmileFields;
    return {
      tenor: p.tenor,
      atm: p.sigma_atm_pct ?? 0,
      fair: p.sigma_fair_q_pct ?? p.sigma_fair_pct ?? p.sigma_atm_pct ?? 0,
      rv: p.rv_pct ?? 0,
      bf25: s.bf_25d_pct ?? 0,
      bf10: s.bf_10d_pct ?? 0,
      rr25: s.rr_25d_pct ?? 0,
      rr10: s.rr_10d_pct ?? 0,
    };
  });
}
