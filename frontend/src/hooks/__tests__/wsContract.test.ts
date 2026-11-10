/**
 * WebSocket message contract (#348, audit 09#5): the frontend message types
 * are checked against the canonical payloads the backend publisher tests use
 * (tests/fixtures/payloads, tests/fixtures/channels). A shape change on either
 * side breaks this test or `tsc`, not the desk at runtime.
 */
import { describe, expect, it } from "vitest";

import account from "../../../../tests/fixtures/channels/account.json";
import positions from "../../../../tests/fixtures/channels/positions.json";
import spotOrders from "../../../../tests/fixtures/channels/spot_orders.json";
import ticks from "../../../../tests/fixtures/payloads/ticks.json";
import type { AccountSnapshotMsg, PositionsChangedMsg, SpotOrderChangedMsg, TickMsg } from "../streams";

describe("ws message types match the publishers' canonical payloads", () => {
  it("ticks: every field the publisher emits is typed, with the right primitive", () => {
    const msg: Required<TickMsg> = ticks satisfies Required<TickMsg>;
    const typed: (keyof TickMsg)[] = ["symbol", "mid", "bid", "ask", "timestamp"];
    expect(Object.keys(msg).sort()).toEqual([...typed].sort());
    expect(Number.isNaN(Date.parse(msg.timestamp))).toBe(false);
  });

  it("execution channels carry the keys the frontend reads", () => {
    const p = positions as unknown as PositionsChangedMsg;
    const a = account as unknown as AccountSnapshotMsg;
    const s = spotOrders as unknown as SpotOrderChangedMsg;
    for (const key of ["event", "source", "rows", "closed", "n_open", "timestamp"] as const) expect(p).toHaveProperty(key);
    for (const key of ["event", "source", "latest", "timestamp"] as const) expect(a).toHaveProperty(key);
    for (const key of ["event", "source", "row", "timestamp"] as const) expect(s).toHaveProperty(key);
    expect([p.event, a.event, s.event]).toEqual(["positions_changed", "account_snapshot", "spot_order_changed"]);
  });
});
