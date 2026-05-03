import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/mocks/handlers";
import { BookPanel } from "../BookPanel";

const P = (id: number, status: string, side = "BUY") => ({
  id,
  symbol: "EURUSD",
  instrument_type: "OPT",
  side,
  quantity: "10",
  strike: "1.08",
  maturity: "2026-05-15",
  option_type: "CALL",
  entry_price: "0.0012",
  entry_timestamp: "2026-04-01T10:00:00Z",
  status,
});

describe("BookPanel", () => {
  beforeEach(() => {
    server.use(
      http.get("*/api/v1/positions", () =>
        HttpResponse.json([P(1, "OPEN", "BUY"), P(2, "CLOSED", "SELL"), P(3, "OPEN", "SELL")]),
      ),
    );
  });
  afterEach(() => server.resetHandlers());

  it("splits rows into Open vs Closed tables with correct counts", async () => {
    render(<BookPanel />);
    await waitFor(() => expect(screen.getByText(/2 open · 1 closed/)).toBeInTheDocument());
    expect(screen.getByText("Open")).toBeInTheDocument();
    expect(screen.getByText("Closed")).toBeInTheDocument();
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
