/**
 * 6w — the builder→backend leg mapper is the one place a UI bug becomes a wrong
 * *order*, so every product's structural mapping (contract/side/tenor/pillar/
 * qty_factor) is pinned here. Prices are intentionally NOT sent: the backend
 * re-prices from the live surface (build_from_legs).
 */
import { describe, expect, it } from "vitest";
import { builderToLegs } from "../orderLegs";

describe("builderToLegs", () => {
  it("vanilla call/put send a single leg with the hand-typed strike (no pillar)", () => {
    expect(builderToLegs("Vanilla Call", "BUY", "3M", "4M", 1.085, "25Δ", "6E (€125k)")).toEqual([
      { contract_type: "call", side: "BUY", tenor: "3M", strike: 1.085 },
    ]);
    expect(builderToLegs("Vanilla Put", "SELL", "2M", "4M", 1.07, "25Δ", "6E (€125k)")).toEqual([
      { contract_type: "put", side: "SELL", tenor: "2M", strike: 1.07 },
    ]);
  });

  it("straddle = ATM call + ATM put, same side", () => {
    const legs = builderToLegs("Straddle", "BUY", "1M", "4M", 0, "25Δ", "6E (€125k)");
    expect(legs.map((l) => [l.contract_type, l.side, l.delta_pillar])).toEqual([
      ["call", "BUY", "atm"],
      ["put", "BUY", "atm"],
    ]);
  });

  it("strangle maps the wing level onto put/call pillars", () => {
    const legs = builderToLegs("Strangle", "BUY", "3M", "4M", 0, "10Δ", "6E (€125k)");
    expect(legs.map((l) => l.delta_pillar)).toEqual(["10dp", "10dc"]);
  });

  it("butterfly = long wings + opposite ATM body at qty_factor 2", () => {
    const legs = builderToLegs("Butterfly", "BUY", "3M", "4M", 0, "25Δ", "6E (€125k)");
    expect(legs).toEqual([
      { contract_type: "call", side: "BUY", tenor: "3M", delta_pillar: "25dc" },
      { contract_type: "call", side: "SELL", tenor: "3M", delta_pillar: "atm", qty_factor: 2 },
      { contract_type: "put", side: "BUY", tenor: "3M", delta_pillar: "25dp" },
    ]);
  });

  it("risk reversal pairs a call against an opposite-side put", () => {
    const legs = builderToLegs("Risk Reversal", "BUY", "2M", "4M", 0, "25Δ", "6E (€125k)");
    expect(legs).toEqual([
      { contract_type: "call", side: "BUY", tenor: "2M", delta_pillar: "25dc" },
      { contract_type: "put", side: "SELL", tenor: "2M", delta_pillar: "25dp" },
    ]);
  });

  it("calendar uses two tenors with opposite near/far sides", () => {
    const legs = builderToLegs("Calendar", "BUY", "1M", "3M", 0, "25Δ", "6E (€125k)");
    expect(legs).toEqual([
      { contract_type: "call", side: "SELL", tenor: "1M", delta_pillar: "atm" },
      { contract_type: "call", side: "BUY", tenor: "3M", delta_pillar: "atm" },
    ]);
  });

  it("future carries the contract size (6E=full, M6E=micro)", () => {
    expect(builderToLegs("Future", "BUY", "3M", "4M", 0, "25Δ", "6E (€125k)")[0]).toMatchObject({
      contract_type: "future", side: "BUY", future_contract_size: "full",
    });
    expect(builderToLegs("Future", "SELL", "3M", "4M", 0, "25Δ", "M6E (€12.5k)")[0]).toMatchObject({
      contract_type: "future", side: "SELL", future_contract_size: "micro",
    });
  });
});
