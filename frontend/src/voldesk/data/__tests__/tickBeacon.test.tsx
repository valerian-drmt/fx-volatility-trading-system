import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type * as Streams from "../../../hooks/streams";
import * as beacon from "../../../utils/beacon";

// Drive the ticks hook directly: the provider's WebSocket is not under test.
vi.mock("../../../hooks/streams", async (orig) => {
  const mod = await orig<typeof Streams>();
  return {
    ...mod,
    useTicks: () => ({ data: { symbol: "EURUSD", mid: 1.1, bid: 1.1, ask: 1.1, timestamp: new Date(Date.now() - 300).toISOString() }, status: "live", asOf: Date.now() - 100, ageMs: 100 }),
  };
});

afterEach(() => vi.restoreAllMocks());

describe("tick fluidity beacon (#374 follow-up)", () => {
  it("a sampled tick posts a ticks beacon with both timings", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.01);
    const post = vi.spyOn(beacon, "postBeacon").mockImplementation(() => undefined);
    const { DataProvider } = await import("../provider");
    render(<DataProvider><div /></DataProvider>);
    await waitFor(() => expect(post).toHaveBeenCalled());
    const ticks = post.mock.calls.map((c) => c[0]).filter((p) => p.channel === "ticks");
    expect(ticks.length).toBeGreaterThan(0);
    expect(ticks[0]!.publish_to_screen_ms).toBeGreaterThanOrEqual(0);
    expect(ticks[0]!.receive_to_screen_ms).toBeGreaterThanOrEqual(0);
  });

  it("an unsampled tick posts nothing", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.9);
    const post = vi.spyOn(beacon, "postBeacon").mockImplementation(() => undefined);
    const { DataProvider } = await import("../provider");
    render(<DataProvider><div /></DataProvider>);
    await new Promise((r) => setTimeout(r, 50));
    expect(post.mock.calls.filter((c) => c[0].channel === "ticks")).toEqual([]);
  });
});
