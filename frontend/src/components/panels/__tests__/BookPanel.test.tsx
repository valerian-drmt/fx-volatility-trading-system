import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/mocks/handlers";
import { BookPanel } from "../BookPanel";

const P = (id: number, side = "BUY") => ({
  id,
  structure: "6EM6",
  side,
  quantity: "10",
  tenor: "1M",
  expiry: "2026-05-15",
  contract_price_entry: "0.0012",
  market_price: "0.0015",
  current_pnl_usd: "30",
  entry_timestamp: "2026-04-01T10:00:00Z",
  updated_at: "2026-04-01T10:00:00Z",
});

describe("BookPanel", () => {
  beforeEach(() => {
    server.use(
      http.get("*/api/v1/positions", () =>
        HttpResponse.json([P(1, "BUY"), P(2, "SELL"), P(3, "SELL")]),
      ),
    );
  });
  afterEach(() => server.resetHandlers());

  it("renders all open positions with a count", async () => {
    render(<BookPanel />);
    await waitFor(() => expect(screen.getByText(/3 open/)).toBeInTheDocument());
    expect(screen.getByTestId("book-panel")).toBeInTheDocument();
  });

  it("colors BUY side positive and SELL side negative", async () => {
    render(<BookPanel />);
    await waitFor(() => expect(screen.getAllByText("BUY").length).toBeGreaterThan(0));
    const panel = screen.getByTestId("book-panel");
    const buys = within(panel).getAllByText("BUY");
    const sells = within(panel).getAllByText("SELL");
    expect(buys[0]).toHaveAttribute("data-sign", "pos");
    expect(sells[0]).toHaveAttribute("data-sign", "neg");
  });
});
