/**
 * Pilot live adapter (R11 PR F): backend `TermStructureResponse` → voldesk
 * `TermPoint[]`. Proves the mock→live swap end-to-end on one typed domain.
 *
 * Mapping: atm←sigma_atm_pct, fair←sigma_fair_q_pct||sigma_fair_pct, rv←rv_pct.
 * The butterfly/risk-reversal fields (bf25/bf10/rr25/rr10) are NOT carried by the
 * term-structure endpoint — they live on the smile and are filled by the Signals
 * wiring (PR 1). They default to 0 here.
 */
import type { TermStructure } from "../../../api/endpoints";
import type { TermPoint } from "../core";

export function adaptTermStructure(resp: TermStructure): TermPoint[] {
  return resp.pillars.map((p) => ({
    tenor: p.tenor,
    atm: p.sigma_atm_pct ?? 0,
    fair: p.sigma_fair_q_pct ?? p.sigma_fair_pct ?? p.sigma_atm_pct ?? 0,
    rv: p.rv_pct ?? 0,
    bf25: 0,
    bf10: 0,
    rr25: 0,
    rr10: 0,
  }));
}
