/**
 * The ticker badge must say "Market closed" on a weekend, not "no feed": a
 * quiet stream while the market is closed is the normal state, a quiet stream
 * while it is open is the problem.
 */
import { describe, expect, it } from "vitest";
import { closedBanner, feedState, isFxMarketOpen } from "../marketHours";

const at = (iso: string): Date => new Date(iso);

describe("isFxMarketOpen", () => {
  it("closes Friday 22:00 UTC and reopens Sunday 22:00 UTC", () => {
    expect(isFxMarketOpen(at("2026-11-06T21:59:00Z"))).toBe(true); // Friday before close
    expect(isFxMarketOpen(at("2026-11-06T22:00:00Z"))).toBe(false); // Friday at close
    expect(isFxMarketOpen(at("2026-11-07T09:51:00Z"))).toBe(false); // Saturday
    expect(isFxMarketOpen(at("2026-11-08T21:59:00Z"))).toBe(false); // Sunday before open
    expect(isFxMarketOpen(at("2026-11-08T22:00:00Z"))).toBe(true); // Sunday open
    expect(isFxMarketOpen(at("2026-11-10T03:00:00Z"))).toBe(true); // Tuesday night
  });
});

describe("feedState", () => {
  it("says Market closed on a weekend whatever the stream did", () => {
    expect(feedState("missing", false)).toEqual({ label: "Market closed", tone: "closed" });
    expect(feedState("stale", false)).toEqual({ label: "Market closed", tone: "closed" });
  });
  it("keeps no feed / feed stale for a quiet stream while the market is open", () => {
    expect(feedState("missing", true)).toEqual({ label: "no feed", tone: "down" });
    expect(feedState("stale", true)).toEqual({ label: "feed stale", tone: "warn" });
  });
  it("a live stream is Market open even if the calendar disagrees (holiday session, clock skew)", () => {
    expect(feedState("live", false)).toEqual({ label: "Market open", tone: "open" });
  });
});

describe("closedBanner", () => {
  const friday = Date.UTC(2026, 10, 6, 21, 59);
  it("is silent while the market is open", () => {
    expect(closedBanner(true, friday, "last-complete")).toBeNull();
  });
  it("names the last complete grid and its UTC stamp while closed", () => {
    expect(closedBanner(false, friday, "last-complete")).toBe("Markets closed · last complete grid from 06 Nov, 21:59 UTC");
    expect(closedBanner(false, friday, "live")).toBe("Markets closed · grid from 06 Nov, 21:59 UTC");
  });
  it("says so when no grid has been persisted yet", () => {
    expect(closedBanner(false, null, null)).toBe("Markets closed · no complete grid yet");
  });
});
