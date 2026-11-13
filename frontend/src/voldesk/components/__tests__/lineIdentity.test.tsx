import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { adaptPositions } from "../../data/live/trade";
import { PositionBreakdown } from "../PositionBreakdown";
import { OpenPositionsTable } from "../PositionsTable";
import { adaptIdent, groupLines } from "../lineIdent";

// The three position panels show the same trades and lines with the SAME
// first columns (Trade · Contract · Product · Structure · Side · Qty · Tenor);
// only the columns after them differ. Prod, 2026-11-12: trade 60 (a strangle
// with one leg left) was a group "1/2 legs" on one panel and a bare
// "Vanilla Call" on another.
const ident = (over: Record<string, unknown>) => ({
  trade_id: 60, contract_id: 747303265, symbol: "EUUZ6 C1210", structure_type: "long strangle 10d",
  structure_label: null, leg_label: "Vanilla Call", side: "BUY", qty: 100, tenor: "1M",
  legs_open: 1, legs_total: 2, ...over,
});
const row = (id: number, over: Record<string, unknown>, identity: Record<string, unknown>) => ({
  id, trade_id: identity.trade_id, contract_id: identity.contract_id, structure: identity.symbol,
  product_label: identity.leg_label, side: identity.side, quantity: identity.qty, tenor_now: identity.tenor,
  delta_usd: 1, current_pnl_usd: 1, iv: 0.06, nominal_eur: 1_250_000,
  line_id: `${identity.symbol}|T${identity.trade_id}`, line_status: "matched", position_id: id,
  identity, ...over,
});
const RAW = [
  row(41, {}, ident({})),
  row(48, {}, ident({ trade_id: 79, contract_id: 853091580, symbol: "EUUG7 C1110", structure_type: "butterfly", qty: 10, tenor: "3M", legs_open: 3, legs_total: 3 })),
  row(50, {}, ident({ trade_id: 79, contract_id: 853091540, symbol: "EUUG7 C1130", structure_type: "butterfly", side: "SELL", qty: 20, tenor: "3M", legs_open: 3, legs_total: 3 })),
  row(49, {}, ident({ trade_id: 79, contract_id: 853091586, symbol: "EUUG7 C1155", structure_type: "butterfly", qty: 10, tenor: "3M", legs_open: 3, legs_total: 3 })),
  row(51, { iv: null }, ident({ trade_id: 88, contract_id: 550123082, symbol: "6EH7", structure_type: "long future", leg_label: "Future - 6E", qty: 7, tenor: "4M", legs_open: 1, legs_total: 1 })),
];

/** The text of the seven identity cells of every row of a table. */
function identityColumns(container: HTMLElement): string[][] {
  return [...container.querySelectorAll("tbody tr")].map((tr) =>
    [...tr.querySelectorAll("td")].slice(0, 7).map((td) => (td.textContent ?? "").replace(/filled .*$/, "").trim()),
  );
}

describe("identity columns shared by the position panels", () => {
  it("names a trade from its booked type, with the legs left", () => {
    const i = adaptIdent(ident({}))!;
    expect(i.tradeName).toBe("Strangle 10Δ");
    expect([i.legsOpen, i.legsTotal]).toEqual([1, 2]);
    expect(adaptIdent(null)).toBeUndefined();
  });

  it("orders and groups the lines one way: newest trade first, legs by symbol", () => {
    const groups = groupLines(adaptPositions(RAW, Date.now()));
    expect(groups.map((g) => [g.tradeId, g.legs.map((l) => l.ident!.symbol)])).toEqual([
      ["88", ["6EH7"]],
      ["79", ["EUUG7 C1110", "EUUG7 C1130", "EUUG7 C1155"]],
      ["60", ["EUUZ6 C1210"]],
    ]);
  });

  it("draws the same seven first columns on the Trade and Risk tables", () => {
    const positions = adaptPositions(RAW, Date.now());
    const trade = render(<OpenPositionsTable positions={positions} showNet={false} />);
    const tradeCols = identityColumns(trade.container);
    trade.unmount();
    const risk = render(<PositionBreakdown positions={positions} />);
    const riskCols = identityColumns(risk.container);

    expect(tradeCols).toEqual(riskCols);
    expect(riskCols).toEqual([
      ["#88", "550123082", "Future", "6EH7", "BUY", "7", "4M"],
      ["▸#79", "3/3 legs", "Butterfly", "—", "BUY", "10", "3M"],
      // one leg of a two-leg trade left: a plain row that still says so, on every panel
      ["#60", "747303265", "Strangle 10Δ1/2 legs", "EUUZ6 C1210", "BUY", "100", "1M"],
    ]);
  });

  it("shows a position being closed as closing, with a cancel, never as rejected", () => {
    // Position -> Close -> IB confirms it holds the order -> the line says so
    // on every panel, and the button left is to cancel that order.
    const closing = { phase: "waiting", qty: 100, n_orders: 1, limit_price: 0.0001, structure_ids: [95] };
    const positions = adaptPositions([row(41, {}, ident({ closing }))], Date.now());
    const onCancelClose = vi.fn();
    const onClose = vi.fn();
    const trade = render(
      <OpenPositionsTable positions={positions} showNet={false} onClose={onClose} onCancelClose={onCancelClose} />,
    );
    expect(screen.getByText("closing · waiting at IB")).toHaveAttribute("data-closing", "waiting");
    expect(screen.queryByText("Close")).toBeNull();
    fireEvent.click(screen.getByText("Cancel close"));
    expect(onCancelClose).toHaveBeenCalledWith([95]);
    expect(onClose).not.toHaveBeenCalled();
    trade.unmount();

    render(<PositionBreakdown positions={positions} />);
    expect(screen.getByText("closing · waiting at IB")).toBeInTheDocument();
  });

  it("warns when two close orders are working on one position", () => {
    const closing = { phase: "waiting", qty: 200, n_orders: 2, limit_price: 0.0001, structure_ids: [89, 95] };
    render(<PositionBreakdown positions={adaptPositions([row(41, {}, ident({ closing }))], Date.now())} />);
    expect(screen.getByText("closing · waiting at IB · 2 orders")).toHaveClass("tag-warn");
  });
});
