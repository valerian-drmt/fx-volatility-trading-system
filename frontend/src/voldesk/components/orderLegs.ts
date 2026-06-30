/**
 * Map the product-driven OrderBuilder selection onto backend free legs
 * (LegSpec / PreviewLeg). Structure only — the backend re-prices from the live
 * surface (build_from_legs), so we never send client strikes/premiums, except a
 * discretionary vanilla strike the trader typed by hand. Pure + unit-tested
 * (the one place a UI choice becomes a real order).
 */
import type { PreviewLeg } from "../../api/endpoints";

export function builderToLegs(
  product: string, side: string, tenor: string, farTenor: string,
  strike: number, wing: string, csize: string,
): PreviewLeg[] {
  const sd = side as "BUY" | "SELL";
  const opp: "BUY" | "SELL" = side === "BUY" ? "SELL" : "BUY";
  // `wing` is a delta pillar ("25Δc"/"ATM") or a bare level ("25Δ"). Symmetric
  // wings use the level → put/call pillars; single-strike structures
  // (straddle/calendar) use the chosen pillar directly.
  const atmWing = wing === "ATM";
  const lvl = atmWing ? "" : wing.replace(/[pc]$/, "").replace("Δ", "").trim(); // "25Δc"/"25Δ" → "25"
  const dc = atmWing ? "atm" : `${lvl}dc`, dp = atmWing ? "atm" : `${lvl}dp`;
  const single = atmWing ? "atm" : wing.toLowerCase().replace("δ", "d");        // "25Δc" → "25dc"
  switch (product) {
    case "Vanilla Call": return [{ contract_type: "call", side: sd, tenor, strike }];
    case "Vanilla Put":  return [{ contract_type: "put",  side: sd, tenor, strike }];
    case "Straddle": return [
      { contract_type: "call", side: sd, tenor, delta_pillar: single },
      { contract_type: "put",  side: sd, tenor, delta_pillar: single },
    ];
    case "Strangle": return [
      { contract_type: "put",  side: sd, tenor, delta_pillar: dp },
      { contract_type: "call", side: sd, tenor, delta_pillar: dc },
    ];
    case "Butterfly": return [
      { contract_type: "call", side: sd,  tenor, delta_pillar: dc },
      { contract_type: "call", side: opp, tenor, delta_pillar: "atm", qty_factor: 2 },
      { contract_type: "put",  side: sd,  tenor, delta_pillar: dp },
    ];
    case "Risk Reversal": return [
      { contract_type: "call", side: sd,  tenor, delta_pillar: dc },
      { contract_type: "put",  side: opp, tenor, delta_pillar: dp },
    ];
    case "Calendar": return [
      { contract_type: "call", side: opp, tenor, delta_pillar: single },
      { contract_type: "call", side: sd,  tenor: farTenor, delta_pillar: single },
    ];
    case "Future": return [
      { contract_type: "future", side: sd, tenor, future_contract_size: csize.startsWith("M6E") ? "micro" : "full" },
    ];
    default: return [];
  }
}
