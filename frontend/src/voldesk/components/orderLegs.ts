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
  const lvl = wing.replace("Δ", "").trim();   // "25Δ" → "25"
  const dc = `${lvl}dc`, dp = `${lvl}dp`;
  switch (product) {
    case "Vanilla Call": return [{ contract_type: "call", side: sd, tenor, strike }];
    case "Vanilla Put":  return [{ contract_type: "put",  side: sd, tenor, strike }];
    case "Straddle": return [
      { contract_type: "call", side: sd, tenor, delta_pillar: "atm" },
      { contract_type: "put",  side: sd, tenor, delta_pillar: "atm" },
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
      { contract_type: "call", side: opp, tenor, delta_pillar: "atm" },
      { contract_type: "call", side: sd,  tenor: farTenor, delta_pillar: "atm" },
    ];
    case "Future": return [
      { contract_type: "future", side: sd, tenor, future_contract_size: csize.startsWith("M6E") ? "micro" : "full" },
    ];
    default: return [];
  }
}
