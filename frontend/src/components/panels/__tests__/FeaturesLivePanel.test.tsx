import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  type FeaturesPayload,
  FeaturesLivePanel,
} from "../FeaturesLivePanel";

const SAMPLE: FeaturesPayload = {
  features: [
    {
      name: "vol_level", value: 6.25, z: -0.15,
      bucket: "0", delta_z_1h: 0.02, pct: 38, signal: "noise",
      expected_z: {
        mu: null, sigma: null, n_obs: 3, status: "insufficient",
        context: { event_type: "none", days_bucket: 4, tod_bucket: "ny_close" },
      },
      vs_expected: null,
    },
    {
      name: "vol_of_vol", value: 0.18, z: -0.43,
      bucket: "-", delta_z_1h: -0.20, pct: 28, signal: "noise",
      expected_z: null, vs_expected: null,
    },
    {
      name: "term_slope", value: 0.06, z: -2.07,
      bucket: "--", delta_z_1h: 0.05, pct: 3, signal: "tail",
      expected_z: {
        mu: 0.10, sigma: 0.40, n_obs: 42, status: "valid",
        context: { event_type: "FOMC", days_bucket: 1, tod_bucket: "overlap" },
      },
      vs_expected: "underpriced",
    },
  ],
  synthesis: {
    joint_pattern: "(0,-,--)",
    regime: {
      id: 9, name: "stress_local_naissant", family: "B_normal_vol",
      action_default: "size_reduce_monitor",
      asymmetry_note: "transition_to_stressed", intensity_count: 1,
    },
    dominant: "term_slope",
    vs_expected: { feature: "term_slope", delta_sigma: -2.17, label: "underpriced" },
    action: "size × 1.0 + asymmetric calendar",
  },
};

describe("FeaturesLivePanel", () => {
  it("renders 7 column headers + 3 feature rows", () => {
    const { container } = render(<FeaturesLivePanel payload={SAMPLE} />);
    const headers = Array.from(container.querySelectorAll("thead th"))
      .map((el) => el.textContent?.trim() ?? "");
    expect(headers).toEqual([
      "feature", "value", "z (90d)", "bucket", "Δz / 1h", "signal", "expected_z context",
    ]);
    expect(screen.getByTestId("feature-row-vol_level")).toBeInTheDocument();
    expect(screen.getByTestId("feature-row-vol_of_vol")).toBeInTheDocument();
    expect(screen.getByTestId("feature-row-term_slope")).toBeInTheDocument();
  });

  it("renders bucket + signal badges with the right palette label", () => {
    render(<FeaturesLivePanel payload={SAMPLE} />);
    // Bucket badges
    expect(screen.getByTestId("badge-0")).toBeInTheDocument();
    expect(screen.getByTestId("badge--")).toBeInTheDocument();
    expect(screen.getByTestId("badge---")).toBeInTheDocument();   // -- becomes badge---
    // Signal badges (two "noise" rows + one "tail")
    expect(screen.getAllByTestId("badge-noise")).toHaveLength(2);
    expect(screen.getByTestId("badge-tail")).toBeInTheDocument();
  });

  it("renders the synthesis row with regime, dominant, and vs_expected", () => {
    render(<FeaturesLivePanel payload={SAMPLE} />);
    const synth = screen.getByTestId("synthesis-row");
    expect(synth).toHaveTextContent("(0,-,--)");
    expect(synth).toHaveTextContent("stress_local_naissant");
    expect(synth).toHaveTextContent("term_slope");
    expect(synth).toHaveTextContent("underpriced");
  });

  it("renders the empty fallback when payload is null", () => {
    render(<FeaturesLivePanel payload={null} />);
    expect(screen.getByText(/no regime snapshot yet/)).toBeInTheDocument();
  });

  it("renders '—' on insufficient expected_z context", () => {
    render(<FeaturesLivePanel payload={SAMPLE} />);
    const volLevelRow = screen.getByTestId("feature-row-vol_level");
    // The last cell (expected_z context) is "—" because status='insufficient'.
    expect(volLevelRow.textContent).toMatch(/—/);
  });
});
