import type { Data } from "plotly.js";
import { PlotlyChart } from "./PlotlyChart";
import { smileTrace } from "./traces";

export interface SmilePoint {
  strike: number;
  vol: number;
}

export interface SmileChartProps {
  points: SmilePoint[];
  tenor: string;
  fairVol?: number | null;
  rv?: number | null;
  sviCurve?: SmilePoint[] | null;
}

function horizontalRef(
  name: string,
  value: number,
  xs: number[],
  color: string,
  dash: "dash" | "dot",
): Data {
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  return {
    name,
    type: "scatter",
    mode: "lines",
    x: [xMin, xMax],
    y: [value, value],
    line: { color, dash, width: 1 },
    hoverinfo: "y+name",
  };
}

export function SmileChart({ points, tenor, fairVol, rv, sviCurve }: SmileChartProps): JSX.Element {
  if (points.length === 0) {
    return <div className="chart-empty">no smile data for {tenor}</div>;
  }
  const xs = points.map((p) => p.strike);
  const traces: Data[] = [smileTrace(points)];
  if (sviCurve && sviCurve.length > 0) {
    traces.push({
      name: "SVI fit",
      type: "scatter",
      mode: "lines",
      x: sviCurve.map((p) => p.strike),
      y: sviCurve.map((p) => p.vol),
      line: { color: "#a855f7", dash: "solid", width: 2, shape: "spline" },
    });
  }
  if (fairVol != null) traces.push(horizontalRef("σ fair (GARCH)", fairVol, xs, "#f59e0b", "dash"));
  if (rv != null) traces.push(horizontalRef("RV (Yang-Zhang)", rv, xs, "#94a3b8", "dot"));
  return (
    <PlotlyChart
      data={traces}
      layout={{
        xaxis: { title: { text: "Strike" } },
        yaxis: { title: { text: `σ (${tenor})` } },
        showlegend: true,
        legend: { orientation: "h", y: -0.2 },
      }}
    />
  );
}
