/**
 * useFetch error semantics (remediation 05 WI-2 part A): a rejected reload must
 * KEEP the last-known data and flag the slice "stale" — never blank it (that
 * used to flip views onto fabricated mock fallbacks). A fetch that never
 * succeeded stays an honest "missing".
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useFetch, type FetchResult } from "../useFetch";

function Probe({ fetcher, sink }: {
  fetcher: () => Promise<number>;
  sink: { current: FetchResult<number> | null };
}): JSX.Element {
  const res = useFetch<number>(fetcher, 60_000);
  sink.current = res;
  return (
    <div>
      <span data-testid="status">{res.status}</span>
      <span data-testid="data">{res.data ?? "none"}</span>
    </div>
  );
}

describe("useFetch", () => {
  it("keeps last-known data and flips to 'stale' on a rejected reload", async () => {
    let fail = false;
    const fetcher = (): Promise<number> =>
      fail ? Promise.reject(new Error("backend down")) : Promise.resolve(42);
    const sink: { current: FetchResult<number> | null } = { current: null };
    render(<Probe fetcher={fetcher} sink={sink} />);
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("42"));
    expect(screen.getByTestId("status").textContent).toBe("live");

    fail = true;
    act(() => sink.current!.reload());
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("stale"));
    // the last-known value survives the failed reload
    expect(screen.getByTestId("data").textContent).toBe("42");
  });

  it("reports 'missing' when the fetch never succeeded", async () => {
    const fetcher = (): Promise<number> => Promise.reject(new Error("cold"));
    const sink: { current: FetchResult<number> | null } = { current: null };
    render(<Probe fetcher={fetcher} sink={sink} />);
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("missing"));
    expect(screen.getByTestId("data").textContent).toBe("none");
  });
});
