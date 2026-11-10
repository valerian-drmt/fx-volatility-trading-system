import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EventsAttribution, FRED_NOTICE, MarketDataAttribution } from "../DataAttribution";

describe("data attribution (#307)", () => {
  it("names every calendar source with the FRED notice and the OGL statement", () => {
    render(<EventsAttribution />);
    const text = screen.getByTestId("events-attribution").textContent ?? "";
    for (const source of ["FRED", "Federal Reserve", "ECB", "Bank of England", "Eurostat", "ONS", "Open Government Licence v3.0"]) {
      expect(text).toContain(source);
    }
    expect(text).toContain(FRED_NOTICE);
  });

  it("says the market data is IB, delayed, on a paper account", () => {
    render(<MarketDataAttribution />);
    expect(screen.getByTestId("market-attribution")).toHaveTextContent("IB, delayed · paper");
  });
});
