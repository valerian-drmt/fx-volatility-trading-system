/**
 * #348 (audit 09#6 / 09#7): useFetch hands back the same object until something
 * changes (the provider memoises on it), and a fetch that fails before any data
 * arrived says "error" instead of "no data".
 */
import { render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it } from "vitest";

import { FreshBadge } from "../../voldesk/components/FreshBadge";
import { useFetch, type FetchResult } from "../useFetch";

function Identity({ seen, fetcher }: { seen: FetchResult<number>[]; fetcher: () => Promise<number> }): JSX.Element {
  const res = useFetch(fetcher, 60_000);
  useEffect(() => {
    seen.push(res);
  });
  return <span data-testid="v">{res.data ?? "none"}</span>;
}

function Failing(): JSX.Element {
  const res = useFetch<number>(() => Promise.reject(new Error("API 502 /api/v1/portfolio/var")), 60_000);
  return <FreshBadge fresh={res} label="VaR" />;
}

describe("useFetch contract", () => {
  it("returns a stable object across renders that change nothing", async () => {
    const seen: FetchResult<number>[] = [];
    const fetcher = (): Promise<number> => Promise.resolve(7);
    const { rerender } = render(<Identity seen={seen} fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId("v").textContent).toBe("7"));
    // `seen` fills from an effect, which can land after the DOM shows 7 on a slow runner.
    await waitFor(() => expect(seen[seen.length - 1]?.data).toBe(7));
    const before = seen[seen.length - 1];
    rerender(<Identity seen={seen} fetcher={fetcher} />);
    rerender(<Identity seen={seen} fetcher={fetcher} />);
    expect(seen[seen.length - 1]).toBe(before);
  });

  it("a fetch that never succeeded shows an error badge with the reason", async () => {
    render(<Failing />);
    await waitFor(() => expect(screen.getByText(/error/)).toBeInTheDocument());
    expect(screen.getByText(/error/).closest(".fresh-badge")).toHaveAttribute("title", "API 502 /api/v1/portfolio/var");
  });
});
