import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/mocks/handlers";
import { PortfolioPanel } from "../PortfolioPanel";

const ROWS = [
  {
    id: 1,
    structure: "6EM6",
    side: "BUY",
    quantity: "10",
    tenor: "1M",
    expiry: "2026-05-15",
    contract_price_entry: "0.0012",
    current_pnl_usd: "30",
    entry_timestamp: "2026-04-01T10:00:00Z",
    updated_at: "2026-04-01T10:00:00Z",
  },
];

describe("PortfolioPanel", () => {
  beforeEach(() => {
    server.use(http.get("*/api/v1/positions", () => HttpResponse.json(ROWS)));
  });
  afterEach(() => server.resetHandlers());

  it("fetches positions and renders a row per position", async () => {
    render(<PortfolioPanel />);
    await waitFor(() => expect(screen.getByText("6EM6")).toBeInTheDocument());
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
