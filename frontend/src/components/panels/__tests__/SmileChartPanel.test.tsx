import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/mocks/handlers";
import { SmileChartPanel } from "../SmileChartPanel";
import { useSelectionStore } from "../../../store/selectionStore";

const smilePayload = (tenor: string, atm: number) => ({
  symbol: "EURUSD",
  timestamp: "2026-04-22T14:00:00Z",
  tenor,
  dte: null,
  points: [
    { strike: 1.13, iv_pct: atm + 1.3, delta_label: "10P" },
    { strike: 1.15, iv_pct: atm + 0.4, delta_label: "25P" },
    { strike: 1.17, iv_pct: atm, delta_label: "ATM" },
    { strike: 1.19, iv_pct: atm + 0.1, delta_label: "25C" },
    { strike: 1.22, iv_pct: atm + 1.0, delta_label: "10C" },
  ],
});

describe("SmileChartPanel", () => {
  beforeEach(() => {
    useSelectionStore.setState({ symbol: "EURUSD", tenor: "1M", strike: null });
    server.use(
      http.get("*/api/v1/vol/smile/1M", () => HttpResponse.json(smilePayload("1M", 6.0))),
      http.get("*/api/v1/vol/smile/3M", () => HttpResponse.json(smilePayload("3M", 7.2))),
    );
  });

  afterEach(() => {
    useSelectionStore.setState({ symbol: "EURUSD", tenor: "1M", strike: null });
  });

  it("renders a tenor select with the 6 standard pillars", () => {
    render(<SmileChartPanel />);
    const select = screen.getByTestId("smile-tenor-select") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toEqual(["1M", "2M", "3M", "4M", "5M", "6M"]);
    expect(select.value).toBe("1M");
  });

  it("re-fetches the smile when the tenor selector changes", async () => {
    render(<SmileChartPanel />);
    // Initial fetch for 1M — title shows "Smile" + selector = 1M.
    await waitFor(() =>
      expect((screen.getByTestId("smile-tenor-select") as HTMLSelectElement).value).toBe("1M"),
    );
    const select = screen.getByTestId("smile-tenor-select") as HTMLSelectElement;
    await userEvent.selectOptions(select, "3M");
    await waitFor(() => expect(useSelectionStore.getState().tenor).toBe("3M"));
    expect((screen.getByTestId("smile-tenor-select") as HTMLSelectElement).value).toBe("3M");
  });

  it("renders a skew table with one row per pillar and skew vs ATM in bp", async () => {
    render(<SmileChartPanel />);
    const table = await screen.findByTestId("smile-table");
    // Wait for the API fetch to populate the rows.
    await waitFor(() => expect(table.querySelectorAll("tbody tr").length).toBe(5));

    const rows = Array.from(table.querySelectorAll("tbody tr")).map((tr) =>
      Array.from(tr.querySelectorAll("td")).map((td) => td.textContent),
    );
    // atm=6.0, wings 10P=7.3 (+130bp), 25P=6.4 (+40bp), 25C=6.1 (+10bp), 10C=7.0 (+100bp).
    expect(rows[2]).toEqual(["ATM", "1.1700", "6.00%", "—"]);
    expect(rows[0][0]).toBe("10P");
    expect(rows[0][3]).toBe("+130 bp");
    expect(rows[4][0]).toBe("10C");
    expect(rows[4][3]).toBe("+100 bp");
  });
});
