import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SmileChart, type SmilePoint } from "../SmileChart";
import { smileTrace } from "../traces";

// Plotly doesn't run under jsdom (no canvas) — swap it out for a div that
// captures the data prop so we can assert on what we'd have rendered.
vi.mock("../PlotlyChart", () => ({
  PlotlyChart: ({ data }: { data: unknown }) => (
    <div data-testid="plotly-stub" data-points={JSON.stringify(data)} />
  ),
}));

const FIXTURE: SmilePoint[] = [
  { strike: 1.07, vol: 0.08 },
  { strike: 1.08, vol: 0.072 },
  { strike: 1.09, vol: 0.079 },
];

describe("smileTrace", () => {
  it("maps strikes to x and vols to y in order", () => {
    const t = smileTrace(FIXTURE) as { x: number[]; y: number[] };
    expect(t.x).toEqual([1.07, 1.08, 1.09]);
    expect(t.y).toEqual([0.08, 0.072, 0.079]);
  });

  it("returns empty arrays for empty input", () => {
    const t = smileTrace([]) as { x: number[]; y: number[] };
    expect(t.x).toEqual([]);
    expect(t.y).toEqual([]);
  });
});

describe("SmileChart", () => {
  it("renders the plotly wrapper with one trace when points exist", () => {
    render(<SmileChart points={FIXTURE} tenor="1M" />);
    const stub = screen.getByTestId("plotly-stub");
    expect(stub).toBeInTheDocument();
    const traces = JSON.parse(stub.getAttribute("data-points") ?? "[]");
    expect(traces).toHaveLength(1);
  });

  it("shows the empty placeholder when no points", () => {
    render(<SmileChart points={[]} tenor="1M" />);
    expect(screen.getByText(/no smile data for 1M/)).toBeInTheDocument();
    expect(screen.queryByTestId("plotly-stub")).not.toBeInTheDocument();
  });
});
