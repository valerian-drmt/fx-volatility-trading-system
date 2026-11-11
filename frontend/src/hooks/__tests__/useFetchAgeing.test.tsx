/**
 * #369 (T5): freshness ages on its own. A slice fetched once must turn "stale"
 * when its warn window passes with no re-render trigger, and a server `asOf`
 * (when the payload carries one) is what the age is measured from.
 */
import type { JSX } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deriveFresh, useFetch } from "../useFetch";

function Probe({ fetcher, warnMs, asOfFrom }: {
  fetcher: () => Promise<{ v: number; ts?: number }>;
  warnMs: number;
  asOfFrom?: (d: { v: number; ts?: number }) => number | null;
}): JSX.Element {
  const res = useFetch(fetcher, warnMs, true, 0, asOfFrom);
  return (
    <div>
      <span data-testid="status">{res.status}</span>
      <span data-testid="age">{res.ageMs ?? "none"}</span>
      <span data-testid="data">{res.data?.v ?? "none"}</span>
    </div>
  );
}

describe("useFetch ageing clock", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("turns stale by itself once the warn window passes, keeping the data", async () => {
    render(<Probe fetcher={() => Promise.resolve({ v: 7 })} warnMs={1500} />);
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("7"));
    expect(screen.getByTestId("status").textContent).toBe("live");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(screen.getByTestId("status").textContent).toBe("stale");
    expect(screen.getByTestId("data").textContent).toBe("7");
    expect(Number(screen.getByTestId("age").textContent)).toBeGreaterThanOrEqual(3000);
  });

  it("measures the age from the server stamp when the payload carries one", async () => {
    const serverAsOf = Date.now() - 10 * 60_000; // grid computed 10 min ago
    render(
      <Probe
        fetcher={() => Promise.resolve({ v: 1, ts: serverAsOf })}
        warnMs={60_000}
        asOfFrom={(d) => d.ts ?? null}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("1"));
    // already past the window at fetch time: stale right away, age ~10 min
    expect(screen.getByTestId("status").textContent).toBe("stale");
    expect(Number(screen.getByTestId("age").textContent)).toBeGreaterThanOrEqual(10 * 60_000 - 1000);
  });
});

describe("deriveFresh", () => {
  it("errored keeps the data and forces stale; missing without data", () => {
    const now = 1_000_000;
    expect(deriveFresh({ data: 1, asOf: now - 100, errored: true }, 60_000, now).status).toBe("stale");
    expect(deriveFresh({ data: 1, asOf: now - 100, errored: false }, 60_000, now)).toMatchObject({ status: "live", ageMs: 100 });
    expect(deriveFresh({ data: null, asOf: null, errored: false }, 60_000, now).status).toBe("missing");
  });
});
