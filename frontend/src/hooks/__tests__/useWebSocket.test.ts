import { describe, expect, it } from "vitest";
import { computeBackoff, MIN_BACKOFF_MS, MAX_BACKOFF_MS } from "../useWebSocket";

describe("computeBackoff", () => {
  it("starts at MIN_BACKOFF_MS for attempt 0", () => {
    expect(computeBackoff(0)).toBe(MIN_BACKOFF_MS);
  });

  it.each([
    [0, 1_000],
    [1, 2_000],
    [2, 4_000],
    [3, 8_000],
    [4, 16_000],
    [5, 32_000],
  ])("doubles on each attempt (attempt=%i → %ims)", (attempt, expected) => {
    expect(computeBackoff(attempt)).toBe(expected);
  });

  it("caps at MAX_BACKOFF_MS once the doubling exceeds it", () => {
    expect(computeBackoff(6)).toBe(MAX_BACKOFF_MS);
    expect(computeBackoff(20)).toBe(MAX_BACKOFF_MS);
  });

  it("treats negative attempts as attempt=0 (defensive)", () => {
    expect(computeBackoff(-5)).toBe(MIN_BACKOFF_MS);
  });
});
