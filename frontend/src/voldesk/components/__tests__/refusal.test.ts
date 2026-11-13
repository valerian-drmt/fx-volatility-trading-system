import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/client";
import { explainGateFailure, fmtBudget, refusalReasons, refusalText, type BudgetRow } from "../refusal";

// The refusal of 2026-11-13 09:27 UTC, exactly as the api returned it. The
// ticket printed "pretrade gate failed".
const GATE = new ApiError(400, "/api/v1/trade/submit", {
  detail: {
    error: "pretrade_gate_failed",
    failures: ["cap_delta", "cap_vega", "cap_gamma"],
    details: {
      caps: { delta_usd: 90645.12, max_notional_eur: 5000000.0, gamma_usd_per_pip: 1018.33, vega_usd_per_volpt: 1888.44 },
      post_trade: { delta_usd: -2575628.94, notional_eur: 5000000.0, gamma_usd_per_pip: 5875.6, vega_usd_per_volpt: 10927.32 },
    },
  },
});

describe("refusal reasons (#478)", () => {
  it("says which of the trader's own limits the trade breaks, with the numbers", () => {
    expect(refusalReasons(GATE)).toEqual([
      "Delta after the trade -$2.58M, your limit $90.6k",
      "Vega after the trade $10.9k per vol point, your limit $1.9k per vol point",
      "Gamma after the trade $5.9k per pip, your limit $1.0k per pip",
    ]);
    expect(refusalText(GATE)).not.toContain("pretrade gate failed");
  });

  it("says how old the surface was", () => {
    const e = new ApiError(400, "/x", {
      detail: { error: "revalidation_failed", reason: "surface_stale", details: { surface_age_seconds: 131.79, max_iv_age_seconds: 120.0 } },
    });
    expect(refusalReasons(e)).toEqual(["The vol surface is 132 s old, limit 120 s"]);
  });

  it("names the unpriced position that leaves the book figures incomplete", () => {
    expect(explainGateFailure("book_partially_priced", { book_unpriced_lines: ["EUR.USD"] }))
      .toBe("A held position has no delta, so the book figures are incomplete (EUR.USD)");
  });

  // Owner's rule, 2026-11-13: the application computes no limit. What it
  // words are facts, and limits the trader typed in himself.
  it("words an account fact and the trader's own limit", () => {
    const d = { premium_to_pay_usd: 250000, net_liquidation_usd: 170000, order_contracts: 500, max_order_contracts: 300 };
    expect(explainGateFailure("insufficient_funds", d))
      .toBe("The premium to pay, $250.0k, is more than the account is worth ($170.0k)");
    expect(explainGateFailure("cap_contracts", d)).toBe("500 contracts in one order, your limit 300");
  });

  it("keeps the reason and the trace id of the other refusals", () => {
    const down = new ApiError(503, "/x", { detail: { error: "ib_disconnected", reason: "heartbeat_stale" } });
    expect(refusalText(down)).toBe("ib disconnected: heartbeat stale");
    const boom = new ApiError(500, "/x", { detail: { error: "trade_submit_unhandled_exception", trace_id: "abc123" } });
    expect(refusalText(boom)).toContain("trace abc123");
    expect(refusalText(new ApiError(504, "/x", { detail: "close in flight: do not retry" }))).toBe("close in flight: do not retry");
    expect(refusalText(new ApiError(502, "/x", null))).toBe("request failed (502)");
  });

  it("shows an unknown code as it came, never nothing", () => {
    expect(explainGateFailure("some_new_check", {})).toBe("some new check");
  });

  it("formats a budget row in its own unit", () => {
    const row = (key: string): BudgetRow => ({ key, label: "", unit: "", cap: 1, before: 0, trade: 0, after: 0, used: 0, breached: false });
    expect(fmtBudget(row("notional"), 5_000_000)).toBe("€5.00M");
    expect(fmtBudget(row("vega"), 10927.32)).toBe("$10.9k");
    expect(fmtBudget(row("delta"), null)).toBe("—");
  });
});
