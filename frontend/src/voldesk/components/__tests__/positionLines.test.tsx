import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { adaptPositions } from "../../data/live/trade";
import { LineStatusTag } from "../LineStatus";
import { PositionBreakdown } from "../PositionBreakdown";
import { UnpricedNotice } from "../UnpricedNotice";
import { groupByTradeId } from "../tradeGrouping";

// The three position panels list the same lines; a line the book and the
// broker disagree on carries one badge, and an unknown number is a dash.
const RAW = [
  {
    id: 51, trade_id: 60, structure: "EUUZ6 C1210", product_label: "Strangle", side: "BUY", quantity: 100,
    delta_usd: 1000, current_pnl_usd: 50, iv: 0.06, nominal_eur: 12_500_000,
    line_id: "EUUZ6 C1210|T60", line_status: "matched", position_id: 51,
  },
  {
    id: -75, trade_id: 60, structure: "EUUZ6 P1075", product_label: "Strangle", side: "BUY", quantity: 100,
    delta_usd: null, current_pnl_usd: null, iv: null, nominal_eur: null,
    line_id: "EUUZ6 P1075|T60", line_status: "book_only", position_id: null,
  },
  {
    id: 54, trade_id: null, structure: "EUR.USD", product_label: "Spot EUR/USD", side: "SELL", quantity: 22533,
    delta_usd: -25000, current_pnl_usd: 12, iv: null, nominal_eur: 22533,
    line_id: "EUR.USD|-", line_status: "cash", position_id: 54,
  },
  {
    id: 52, trade_id: null, structure: "EUUG7 P1120", product_label: "Vanilla Put", side: "BUY", quantity: 10,
    delta_usd: -500, current_pnl_usd: 1, iv: 0.058, nominal_eur: 1_250_000,
    line_id: "EUUG7 P1120|-", line_status: "broker_only", position_id: 52,
  },
];

describe("position lines (#461)", () => {
  it("carries the line id and status, and flags what nothing priced", () => {
    const rows = adaptPositions(RAW, Date.now());
    expect(rows.map((r) => r.lineId)).toEqual(RAW.map((r) => r.line_id));
    expect(rows[1]).toMatchObject({ lineStatus: "book_only", unpriced: true, netted: true });
    expect(rows[0]).toMatchObject({ lineStatus: "matched", unpriced: false });
  });

  it("keeps rows without a trade as separate lines", () => {
    const groups = groupByTradeId(adaptPositions(RAW, Date.now()));
    // trade 60 (2 legs), the spot balance, the unexplained put: three groups,
    // not "trade 60" plus one shared group for everything without a trade
    expect(groups.map((g) => g.legs.length)).toEqual([2, 1, 1]);
    expect(groups.map((g) => g.tradeId)).toEqual(["60", null, null]);
  });

  it("shows a badge for a break, none for a matched line", () => {
    const { container, rerender } = render(<LineStatusTag status="matched" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<LineStatusTag status="book_only" />);
    expect(screen.getByText("book only")).toHaveAttribute("data-line-status", "book_only");
    rerender(<LineStatusTag status="cash" />);
    expect(screen.getByText("cash")).toBeInTheDocument();
  });

  it("prints an unknown delta and P&L as a dash on the Risk table", () => {
    const all = adaptPositions(RAW, Date.now());
    const put = { ...all.find((r) => r.lineId === "EUUG7 P1120|-")!, unpriced: true };
    const spot = all.find((r) => r.lineId === "EUR.USD|-")!;
    const { container } = render(<PositionBreakdown positions={[put, spot]} />);
    // cash is not a contract: no row for the spot balance
    expect(container.querySelector('tr[data-line-id="EUR.USD|-"]')).toBeNull();
    const tr = container.querySelector('tr[data-line-id="EUUG7 P1120|-"]')!;
    const cells = [...tr.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells[8]).toBe("—");                  // delta
    expect(cells[cells.length - 1]).toBe("—");   // P&L
    expect(screen.getByText("not booked")).toBeInTheDocument();
  });

  it("says which lines are missing from the net greeks (#459)", () => {
    const rows = adaptPositions(RAW, Date.now());
    // a "book only" line is not held: it blocks nothing and is not announced
    const { container, rerender } = render(<UnpricedNotice positions={rows} />);
    expect(container).toBeEmptyDOMElement();
    // a position the broker holds and nothing priced is
    const held = { ...rows.find((r) => r.lineId === "EUUG7 P1120|-")!, unpriced: true };
    rerender(<UnpricedNotice positions={[...rows, held]} />);
    expect(screen.getByTestId("unpriced-notice")).toHaveTextContent("1 held position not priced (EUUG7 P1120)");
  });
});
