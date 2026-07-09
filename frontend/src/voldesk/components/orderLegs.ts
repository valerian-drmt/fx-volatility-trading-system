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
  spreadSide = "call", spreadNear = "ATM", spreadFar = "25Δ",
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
      // put + call at different strikes ; force 25Δ if ATM (else it's a straddle)
      { contract_type: "put",  side: sd, tenor, delta_pillar: atmWing ? "25dp" : dp },
      { contract_type: "call", side: sd, tenor, delta_pillar: atmWing ? "25dc" : dc },
    ];
    case "Straddle/Strangle": return atmWing
      ? [ // 50Δ (ATM) → straddle : call + put at the same ATM strike
          { contract_type: "call", side: sd, tenor, delta_pillar: "atm" },
          { contract_type: "put",  side: sd, tenor, delta_pillar: "atm" },
        ]
      : [ // 10Δ / 25Δ → strangle : OTM put + OTM call
          { contract_type: "put",  side: sd, tenor, delta_pillar: dp },
          { contract_type: "call", side: sd, tenor, delta_pillar: dc },
        ];
    case "Butterfly": return [
      // 3 calls (low + 2× ATM body + high) → classifier names it "butterfly".
      // Force 25Δ wings if ATM is picked, else the 3 legs collapse onto ATM.
      { contract_type: "call", side: sd,  tenor, delta_pillar: atmWing ? "25dp" : dp },
      { contract_type: "call", side: opp, tenor, delta_pillar: "atm", qty_factor: 2 },
      { contract_type: "call", side: sd,  tenor, delta_pillar: atmWing ? "25dc" : dc },
    ];
    case "Risk Reversal": return [
      { contract_type: "call", side: sd,  tenor, delta_pillar: dc },
      { contract_type: "put",  side: opp, tenor, delta_pillar: dp },
    ];
    case "Call Spread": return [
      // long ATM, short OTM call ; never ATM/ATM (zero-width) → force 25Δ if ATM
      { contract_type: "call", side: sd,  tenor, delta_pillar: "atm" },
      { contract_type: "call", side: opp, tenor, delta_pillar: atmWing ? "25dc" : dc },
    ];
    case "Put Spread": return [
      { contract_type: "put", side: sd,  tenor, delta_pillar: "atm" },
      { contract_type: "put", side: opp, tenor, delta_pillar: atmWing ? "25dp" : dp },
    ];
    case "Call/Put Spread": {
      // 2 same-type legs on one wing : near = long (higher |Δ|), far = short
      // (lower |Δ|). Side swaps roles. "25Δ" → "25dc"/"25dp" ; "ATM" → "atm".
      const t: "call" | "put" = spreadSide === "call" ? "call" : "put";
      const suf = spreadSide === "call" ? "dc" : "dp";
      const pil = (l: string): string => (l === "ATM" ? "atm" : l.replace(/[Δδ]/, "") + suf);
      return [
        { contract_type: t, side: sd,  tenor, delta_pillar: pil(spreadNear) },
        { contract_type: t, side: opp, tenor, delta_pillar: pil(spreadFar) },
      ];
    }
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
