import { describe, expect, it } from "vitest";
import { tenorToDays } from "../tenor";

describe("tenorToDays", () => {
  it.each([
    ["1D", 1],
    ["1W", 7],
    ["2W", 14],
    ["1M", 30],
    ["3M", 90],
    ["1Y", 365],
  ])("maps %s to %i days", (tenor, expected) => {
    expect(tenorToDays(tenor)).toBe(expected);
  });

  it("is case-insensitive", () => {
    expect(tenorToDays("1m")).toBe(30);
  });

  it.each(["", "abc", "M1", "1Z", "1.5M"])("returns null for invalid tenor %s", (bad) => {
    expect(tenorToDays(bad)).toBeNull();
  });
});
