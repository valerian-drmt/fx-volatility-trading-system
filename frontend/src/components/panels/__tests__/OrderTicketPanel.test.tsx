import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/mocks/handlers";
import { OrderTicketPanel } from "../OrderTicketPanel";
import { useOrderDraftStore } from "../../../store/orderDraftStore";

const GREEKS = {
  price: 0.00123,
  delta: 0.52,
  gamma: 4.2,
  vega: 0.0021,
  theta: -0.00005,
};

describe("OrderTicketPanel", () => {
  beforeEach(() => {
    useOrderDraftStore.getState().reset();
    server.use(http.post("*/api/v1/greeks", () => HttpResponse.json(GREEKS)));
  });
  afterEach(() => server.resetHandlers());

  it("shows the hint until strike and tenor are both filled", () => {
    render(<OrderTicketPanel />);
    expect(screen.getByText(/fill side, strike and tenor/)).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-greeks")).not.toBeInTheDocument();
  });

  it("calls /greeks when strike + tenor become valid and renders the 5 tiles", async () => {
    const user = userEvent.setup();
    render(<OrderTicketPanel />);

    await user.type(screen.getByLabelText("Strike"), "1.085");
    await user.type(screen.getByLabelText("Tenor"), "1M");

    await waitFor(() =>
      expect(screen.getByTestId("ticket-greeks")).toBeInTheDocument(),
    );
    expect(screen.getByText("0.520")).toBeInTheDocument();
    expect(screen.getByText("4.200")).toBeInTheDocument();
  });

  it("disables the submit button until the draft is valid", async () => {
    const user = userEvent.setup();
    render(<OrderTicketPanel />);
    const submit = screen.getByRole("button", { name: /submit/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("Strike"), "1.08");
    await user.type(screen.getByLabelText("Tenor"), "1M");
    await waitFor(() => expect(submit).not.toBeDisabled());
  });
});
