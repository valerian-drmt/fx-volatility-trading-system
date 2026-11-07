/**
 * #370: the latency beacon samples 1 push in 10 and never reports a timing
 * that cannot be true (negative from clock skew, or absurdly large).
 */
import { describe, expect, it } from "vitest";
import { beaconPayload, SAMPLE_RATE, shouldSample } from "../beacon";

describe("shouldSample", () => {
  it("keeps one in ten", () => {
    expect(SAMPLE_RATE).toBe(0.1);
    expect(shouldSample(0.05)).toBe(true);
    expect(shouldSample(0.1)).toBe(false);
    expect(shouldSample(0.99)).toBe(false);
  });
});

describe("beaconPayload", () => {
  const rendered = 1_700_000_000_000;
  it("reports both timings when both stamps are known", () => {
    expect(beaconPayload("positions", rendered - 850, rendered - 120, rendered)).toEqual({
      channel: "positions",
      publish_to_screen_ms: 850,
      receive_to_screen_ms: 120,
    });
  });
  it("drops a skewed publish stamp but keeps the client-side timing", () => {
    expect(beaconPayload("positions", rendered + 5_000, rendered - 40, rendered)).toEqual({
      channel: "positions",
      publish_to_screen_ms: null,
      receive_to_screen_ms: 40,
    });
  });
  it("returns null when nothing is reportable", () => {
    expect(beaconPayload("positions", null, null, rendered)).toBeNull();
    expect(beaconPayload("positions", rendered - 90_000, null, rendered)).toBeNull();
  });
});
