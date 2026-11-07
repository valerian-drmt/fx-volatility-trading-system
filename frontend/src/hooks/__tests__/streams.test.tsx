/**
 * #367 (T3): the push-channel parsers keep only the execution engine's events
 * (the `positions` channel also carries the position monitor's MTM snapshots),
 * and a desk whose sockets cannot connect still renders its REST data: the
 * chip goes red, nothing blanks.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../tests/mocks/handlers";
import { useDeskData } from "../../voldesk/data/deskData";
import { DataProvider } from "../../voldesk/data/provider";
import { WsChip } from "../../voldesk/components/WsChip";
import { parseAccountSnapshot, parsePositionsChanged, parseSpotOrderChanged } from "../streams";

describe("push-channel parsers", () => {
  it("positions: keeps positions_changed, drops the position monitor's MTM messages and junk", () => {
    const ok = parsePositionsChanged(JSON.stringify({ event: "positions_changed", source: "execution_engine", rows: [], closed: [{ id: 1 }], n_open: 0, timestamp: "t" }));
    expect(ok?.event).toBe("positions_changed");
    expect(parsePositionsChanged(JSON.stringify({ position_id: 52, structure_id: 87, spot: 1.12 }))).toBeNull();
    expect(parsePositionsChanged("not json")).toBeNull();
    expect(parsePositionsChanged("")).toBeNull();
  });

  it("account: needs event + latest", () => {
    expect(parseAccountSnapshot(JSON.stringify({ event: "account_snapshot", source: "x", latest: { net_liq_usd: 1 }, timestamp: "t" }))?.latest.net_liq_usd).toBe(1);
    expect(parseAccountSnapshot(JSON.stringify({ net_liq_usd: 1 }))).toBeNull();
  });

  it("spot orders: needs event + a row with a numeric id", () => {
    expect(parseSpotOrderChanged(JSON.stringify({ event: "spot_order_changed", source: "x", row: { id: 3, state: "filled" }, timestamp: "t" }))?.row.id).toBe(3);
    expect(parseSpotOrderChanged(JSON.stringify({ event: "spot_order_changed", row: { state: "filled" } }))).toBeNull();
  });
});

function Probe(): JSX.Element {
  const { trade, streams } = useDeskData();
  return (
    <div>
      <span data-testid="n">{trade.data?.positions.length ?? "none"}</span>
      <span data-testid="status">{trade.status}</span>
      <WsChip status={streams.positions} />
    </div>
  );
}

describe("desk without a WebSocket server", () => {
  it("keeps the REST positions and shows the push chip as down, never blanks", async () => {
    server.use(
      http.get("*/api/v1/positions/open", () => HttpResponse.json([
        { id: 1, structure: "EUUZ6 C1130", side: "BUY", quantity: 10, tenor: "3M", expiry: "2026-12-04", nominal_eur: 1e6 },
      ])),
    );
    render(<DataProvider><Probe /></DataProvider>);
    await waitFor(() => expect(screen.getByTestId("n").textContent).toBe("1"));
    expect(screen.getByTestId("status").textContent).toBe("live");
    // jsdom opens no real socket: the chip ends red (closed) or amber while the
    // backoff runs; it never claims a live push, and the table above is intact.
    await waitFor(() => expect(screen.getByText(/down · 60 s poll|reconnecting/)).toBeInTheDocument());
    expect(screen.getByTestId("n").textContent).toBe("1");
  });
});
