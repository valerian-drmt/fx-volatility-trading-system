import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/mocks/handlers";
import { PortfolioPanel } from "../PortfolioPanel";

const ROWS = [
  {
    id: 1,
    symbol: "EURUSD",
    instrument_type: "OPT",
    side: "BUY",
    quantity: "10",
    strike: "1.08",
    maturity: "2026-05-15",
    option_type: "CALL",
    entry_price: "0.0012",
    entry_timestamp: "2026-04-01T10:00:00Z",
    status: "OPEN",
  },
];

describe("PortfolioPanel", () => {
  beforeEach(() => {
    server.use(http.get("*/api/v1/positions", () => HttpResponse.json(ROWS)));
  });
  afterEach(() => server.resetHandlers());

  it("fetches positions and renders a row per position", async () => {
    render(<PortfolioPanel />);
    await waitFor(() =>
      expect(screen.getByText("EURUSD")).toBeInTheDocument(),
    );
    expect(screen.getByText("OPEN")).toBeInTheDocument();
    expect(screen.getByTestId("portfolio-panel")).toBeInTheDocument();
  });

  it("shows an error banner when the API returns 500", async () => {
    server.use(
      http.get("*/api/v1/positions", () =>
        HttpResponse.json({ detail: "nope" }, { status: 500 }),
      ),
    );
    render(<PortfolioPanel />);
    await waitFor(() => expect(screen.getByText(/API 500/)).toBeInTheDocument());
  });
});
